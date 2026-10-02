//! Small deterministic CPU rasterizer for native OCCT triangle meshes.
use crate::native_occt::TriangleMesh;
use image::{ImageFormat, Rgb, RgbImage};
use std::io::Cursor;

const MAX_PIXELS: u64 = 16_777_216;
const MAX_TRIANGLES: usize = 5_000_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum View {
    Front,
    Back,
    Right,
    Left,
    Top,
    Bottom,
    Iso,
}

impl View {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value.to_ascii_lowercase().as_str() {
            "front" => Ok(Self::Front),
            "back" => Ok(Self::Back),
            "right" => Ok(Self::Right),
            "left" => Ok(Self::Left),
            "top" => Ok(Self::Top),
            "bottom" => Ok(Self::Bottom),
            "iso" | "isometric" => Ok(Self::Iso),
            _ => Err(format!("unsupported view: {value}")),
        }
    }
    fn basis(self) -> ([f64; 3], [f64; 3], [f64; 3]) {
        let s = std::f64::consts::FRAC_1_SQRT_2;
        match self {
            Self::Front => ([1., 0., 0.], [0., 0., 1.], [0., 1., 0.]),
            Self::Back => ([-1., 0., 0.], [0., 0., 1.], [0., -1., 0.]),
            Self::Right => ([0., -1., 0.], [0., 0., 1.], [1., 0., 0.]),
            Self::Left => ([0., 1., 0.], [0., 0., 1.], [-1., 0., 0.]),
            Self::Top => ([1., 0., 0.], [0., 1., 0.], [0., 0., 1.]),
            Self::Bottom => ([1., 0., 0.], [0., -1., 0.], [0., 0., -1.]),
            Self::Iso => (
                [s, -s, 0.],
                [
                    -1. / 6.0_f64.sqrt(),
                    -1. / 6.0_f64.sqrt(),
                    (2.0_f64 / 3.0).sqrt(),
                ],
                [1.0_f64 / 3.0_f64.sqrt(); 3],
            ),
        }
    }
}

#[derive(Clone, Debug)]
pub struct RenderOptions {
    pub view: View,
    pub width: u32,
    pub height: u32,
    pub margin: f64,
    /// Visible silhouette/depth discontinuities, never tessellation edges.
    pub depth_edges: bool,
}
impl Default for RenderOptions {
    fn default() -> Self {
        Self {
            view: View::Front,
            width: 768,
            height: 768,
            margin: 0.06,
            depth_edges: true,
        }
    }
}

#[derive(Clone, Debug)]
pub struct RenderMetadata {
    pub view: View,
    pub projection: String,
    pub width: u32,
    pub height: u32,
    pub framing_center: [f64; 3],
    pub units_per_pixel: f64,
    pub margin_fraction: f64,
    pub shading: String,
    pub depth_test: String,
}
#[derive(Clone, Debug)]
pub struct RenderedView {
    pub png: Vec<u8>,
    pub metadata: RenderMetadata,
}

pub fn render(mesh: &TriangleMesh, options: &RenderOptions) -> Result<RenderedView, String> {
    validate(mesh, options)?;
    let mut bounds = [[f64::INFINITY, f64::NEG_INFINITY]; 3];
    for p in &mesh.positions {
        for axis in 0..3 {
            bounds[axis][0] = bounds[axis][0].min(p[axis]);
            bounds[axis][1] = bounds[axis][1].max(p[axis]);
        }
    }
    let center = [0, 1, 2].map(|i| (bounds[i][0] + bounds[i][1]) * 0.5);
    let (h, v, d) = options.view.basis();
    let dot = |a: [f64; 3], b: [f64; 3]| a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    let projected_center = [dot(center, h), dot(center, v)];
    let available = (options.width.min(options.height) as f64) * (1.0 - 2.0 * options.margin);
    // The scale is fixed from the 3D bounding-box diagonal, so orthographic views never stretch.
    let bbox_diagonal = ((bounds[0][1] - bounds[0][0]).powi(2)
        + (bounds[1][1] - bounds[1][0]).powi(2)
        + (bounds[2][1] - bounds[2][0]).powi(2))
    .sqrt();
    let units_per_pixel = bbox_diagonal.max(f64::MIN_POSITIVE) / available;
    let mut image = RgbImage::from_pixel(options.width, options.height, Rgb([248, 249, 251]));
    let mut zbuf = vec![f64::NEG_INFINITY; (options.width as usize) * (options.height as usize)];
    for tri in 0..mesh.positions.len() / 3 {
        let base = tri * 3;
        let world = [
            mesh.positions[base],
            mesh.positions[base + 1],
            mesh.positions[base + 2],
        ];
        let screen = world.map(|p| {
            let x =
                (dot(p, h) - projected_center[0]) / units_per_pixel + options.width as f64 * 0.5;
            let y =
                options.height as f64 * 0.5 - (dot(p, v) - projected_center[1]) / units_per_pixel;
            [x, y, dot(p, d)]
        });
        raster_triangle(
            &mut image,
            &mut zbuf,
            screen,
            world,
            mesh.normals[base..base + 3]
                .try_into()
                .unwrap_or([[0.; 3]; 3]),
            d,
        );
    }
    if options.depth_edges {
        mark_depth_edges(&mut image, &zbuf, units_per_pixel);
    }
    let mut png = Cursor::new(Vec::new());
    image
        .write_to(&mut png, ImageFormat::Png)
        .map_err(|e| format!("PNG encoding failed: {e}"))?;
    Ok(RenderedView {
        png: png.into_inner(),
        metadata: RenderMetadata {
            view: options.view,
            projection: "orthographic".into(),
            width: options.width,
            height: options.height,
            framing_center: center,
            units_per_pixel,
            margin_fraction: options.margin,
            shading: "two-sided diffuse; normals only; no fabricated depth cues".into(),
            depth_test: if options.depth_edges {
                "z-buffer, nearest surface wins; silhouette/depth-discontinuity overlay"
            } else {
                "z-buffer, nearest surface wins"
            }
            .into(),
        },
    })
}

fn mark_depth_edges(image: &mut RgbImage, depths: &[f64], units_per_pixel: f64) {
    let width = image.width() as usize;
    let height = image.height() as usize;
    let threshold = units_per_pixel * 2.5;
    for y in 0..height {
        for x in 0..width {
            let current = depths[y * width + x];
            if !current.is_finite() {
                continue;
            }
            let visible_boundary = [
                (x.checked_sub(1), Some(y)),
                (Some(x + 1), Some(y)),
                (Some(x), y.checked_sub(1)),
                (Some(x), Some(y + 1)),
            ]
            .iter()
            .any(|(nx, ny)| match (*nx, *ny) {
                (Some(nx), Some(ny)) if nx < width && ny < height => {
                    let other = depths[ny * width + nx];
                    !other.is_finite() || (current - other).abs() > threshold
                }
                _ => true,
            });
            if visible_boundary {
                let color = image
                    .get_pixel(x as u32, y as u32)
                    .0
                    .map(|c| (f64::from(c) * 0.56) as u8);
                image.put_pixel(x as u32, y as u32, Rgb(color));
            }
        }
    }
}

fn validate(mesh: &TriangleMesh, o: &RenderOptions) -> Result<(), String> {
    if o.width < 16 || o.height < 16 || (o.width as u64) * (o.height as u64) > MAX_PIXELS {
        return Err("render dimensions outside supported limits".into());
    }
    if !o.margin.is_finite() || !(0.0..0.4).contains(&o.margin) {
        return Err("margin must be finite and in [0, 0.4)".into());
    }
    if mesh.positions.is_empty()
        || mesh.positions.len() % 3 != 0
        || mesh.positions.len() / 3 > MAX_TRIANGLES
        || mesh.normals.len() != mesh.positions.len()
    {
        return Err("invalid or oversized triangle mesh".into());
    }
    if mesh
        .positions
        .iter()
        .chain(mesh.normals.iter())
        .flatten()
        .any(|x| !x.is_finite())
    {
        return Err("mesh has non-finite vertices or normals".into());
    }
    let mut extent = [0.0_f64; 3];
    for axis in 0..3 {
        let lo = mesh
            .positions
            .iter()
            .map(|p| p[axis])
            .fold(f64::INFINITY, f64::min);
        let hi = mesh
            .positions
            .iter()
            .map(|p| p[axis])
            .fold(f64::NEG_INFINITY, f64::max);
        extent[axis] = hi - lo;
    }
    if extent.iter().any(|x| !x.is_finite()) || extent.iter().all(|x| *x <= f64::EPSILON) {
        return Err("mesh bounds are degenerate".into());
    }
    Ok(())
}

fn raster_triangle(
    image: &mut RgbImage,
    zbuf: &mut [f64],
    p: [[f64; 3]; 3],
    world: [[f64; 3]; 3],
    normals: [[f64; 3]; 3],
    view: [f64; 3],
) {
    let edge = |a: [f64; 3], b: [f64; 3], x: f64, y: f64| {
        (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
    };
    let area = edge(p[0], p[1], p[2][0], p[2][1]);
    if !area.is_finite() || area.abs() < 1e-9 {
        return;
    }
    let w = image.width() as i32;
    let h = image.height() as i32;
    let minx = p
        .iter()
        .map(|v| v[0].floor() as i32)
        .min()
        .unwrap()
        .clamp(0, w - 1);
    let maxx = p
        .iter()
        .map(|v| v[0].ceil() as i32)
        .max()
        .unwrap()
        .clamp(0, w - 1);
    let miny = p
        .iter()
        .map(|v| v[1].floor() as i32)
        .min()
        .unwrap()
        .clamp(0, h - 1);
    let maxy = p
        .iter()
        .map(|v| v[1].ceil() as i32)
        .max()
        .unwrap()
        .clamp(0, h - 1);
    let face = cross(sub(world[1], world[0]), sub(world[2], world[0]));
    let face = normalize(face).unwrap_or(view);
    let ns = normals.map(normalize).map(|n| n.unwrap_or(face));
    let light = normalize([-0.35, -0.55, 0.76]).unwrap_or([0., 0., 1.]);
    for y in miny..=maxy {
        for x in minx..=maxx {
            let px = x as f64 + 0.5;
            let py = y as f64 + 0.5;
            let a = edge(p[1], p[2], px, py) / area;
            let b = edge(p[2], p[0], px, py) / area;
            let c = 1.0 - a - b;
            if a < -1e-8 || b < -1e-8 || c < -1e-8 {
                continue;
            }
            let depth = a * p[0][2] + b * p[1][2] + c * p[2][2];
            let index = (y as usize) * (w as usize) + x as usize;
            if depth <= zbuf[index] {
                continue;
            }
            zbuf[index] = depth;
            let mut n = normalize([
                a * ns[0][0] + b * ns[1][0] + c * ns[2][0],
                a * ns[0][1] + b * ns[1][1] + c * ns[2][1],
                a * ns[0][2] + b * ns[1][2] + c * ns[2][2],
            ])
            .unwrap_or(face);
            if dot(n, view) < 0.0 {
                n = n.map(|v| -v)
            }
            let diffuse = dot(n, light).max(0.0);
            let intensity = (0.64 + 0.34 * diffuse).clamp(0.0, 1.0);
            image.put_pixel(
                x as u32,
                y as u32,
                Rgb([
                    (190.0 * intensity) as u8,
                    (202.0 * intensity) as u8,
                    (218.0 * intensity) as u8,
                ]),
            );
        }
    }
}
fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn normalize(v: [f64; 3]) -> Option<[f64; 3]> {
    let l = dot(v, v).sqrt();
    (l.is_finite() && l > 1e-15).then(|| v.map(|x| x / l))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn cube() -> TriangleMesh {
        let v = [
            [0., 0., 0.],
            [1., 0., 0.],
            [1., 1., 0.],
            [0., 1., 0.],
            [0., 0., 1.],
            [1., 0., 1.],
            [1., 1., 1.],
            [0., 1., 1.],
        ];
        let faces = [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ];
        let mut positions = Vec::new();
        for f in faces {
            for i in f {
                positions.push(v[i]);
            }
        }
        let normals = positions
            .iter()
            .map(|p| normalize(*p).unwrap_or([0., 0., 1.]))
            .collect();
        TriangleMesh { positions, normals }
    }
    #[test]
    fn view_axes_and_shared_scale_are_consistent() {
        assert_eq!(View::Front.basis().0, [1., 0., 0.]);
        assert_eq!(View::Front.basis().1, [0., 0., 1.]);
        let m = cube();
        let scales = [View::Front, View::Right, View::Top, View::Iso].map(|view| {
            render(
                &m,
                &RenderOptions {
                    view,
                    ..Default::default()
                },
            )
            .unwrap()
            .metadata
            .units_per_pixel
        });
        assert!(scales.iter().all(|s| (*s - scales[0]).abs() < 1e-12));
    }
    #[test]
    fn z_buffer_keeps_near_surface_and_png_is_valid() {
        let mut m = cube();
        for p in &mut m.positions {
            p[1] += 3.0;
        }
        let out = render(
            &m,
            &RenderOptions {
                view: View::Front,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(&out.png[..8], b"\x89PNG\r\n\x1a\n");
        let decoded = image::load_from_memory(&out.png).unwrap().to_rgb8();
        assert_eq!(decoded.dimensions(), (768, 768));
        assert!(decoded.pixels().any(|p| p.0 != [248, 249, 251]));
        assert!(View::Back.basis().2[1] < 0.0);
        assert!(View::Bottom.basis().2[2] < 0.0);
    }
    #[test]
    fn depth_edges_mark_steps_without_marking_coplanar_triangle_boundaries() {
        let mut image = RgbImage::from_pixel(5, 5, Rgb([200, 200, 200]));
        let mut depths = vec![10.; 25];
        mark_depth_edges(&mut image, &depths, 0.1);
        assert_eq!(image.get_pixel(2, 2).0, [200, 200, 200]);
        depths[2 * 5 + 3] = 5.;
        mark_depth_edges(&mut image, &depths, 0.1);
        assert!(image.get_pixel(2, 2).0[0] < 200);
    }
    #[test]
    fn farther_triangle_cannot_overwrite_nearer_surface() {
        let mut image = RgbImage::from_pixel(16, 16, Rgb([0, 0, 0]));
        let mut depths = vec![f64::NEG_INFINITY; 256];
        let points = [[1., 1., 5.], [14., 1., 5.], [1., 14., 5.]];
        let normals = [[0., 0., 1.]; 3];
        raster_triangle(
            &mut image,
            &mut depths,
            points,
            points,
            normals,
            [0., 0., 1.],
        );
        let pixel = *image.get_pixel(4, 4);
        let farther = points.map(|mut p| {
            p[2] = 1.;
            p
        });
        raster_triangle(
            &mut image,
            &mut depths,
            farther,
            farther,
            normals,
            [0., 0., 1.],
        );
        assert_eq!(depths[4 * 16 + 4], 5.);
        assert_eq!(*image.get_pixel(4, 4), pixel);
    }
    #[test]
    fn malformed_mesh_is_rejected() {
        let m = TriangleMesh {
            positions: vec![[f64::NAN, 0., 0.]; 3],
            normals: vec![[0., 0., 1.]; 3],
        };
        assert!(render(&m, &RenderOptions::default()).is_err());
    }
}
