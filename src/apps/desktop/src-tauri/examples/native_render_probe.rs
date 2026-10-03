//! Render an actual OCCT tessellation without Python or a browser.
use ai_modeler_ui_lib::native_occt::{Kernel, Shape};
use ai_modeler_ui_lib::native_render::{self, RenderOptions, View};
use serde_json::json;
use std::{env, fs, path::PathBuf};

fn main() -> Result<(), String> {
    let args: Vec<_> = env::args().skip(1).collect();
    let manifest = PathBuf::from(
        args.first()
            .ok_or("usage: native_render_probe RUNTIME_MANIFEST OUTPUT_DIR")?,
    );
    let output = PathBuf::from(args.get(1).ok_or("missing output directory")?);
    fs::create_dir_all(&output).map_err(|e| e.to_string())?;
    let kernel = Kernel::load_manifest(&manifest)?;

    // Reuse the recessed bridge-profile scene from native_occt_probe.
    let stock = kernel.make_box([100., 40., 120.], [0., 0., 60.])?;
    let mut tools: Vec<Shape> = vec![kernel.make_box([20., 8., 124.], [0., 16., 60.])?];
    for x in [-32., 0., 32.] {
        for (z, radius) in [(22., 6.), (60., 4.), (98., 6.)] {
            tools.push(kernel.cylinder(radius, 44., [x, -22., z], [0., 1., 0.])?);
        }
    }
    let refs: Vec<_> = tools.iter().collect();
    let part = kernel.cut(&stock, &refs)?;
    let mesh = part.tessellate(0.1, 0.5)?;
    let mut results = Vec::new();
    for view in [
        View::Front,
        View::Back,
        View::Right,
        View::Left,
        View::Top,
        View::Bottom,
        View::Iso,
    ] {
        let rendered = native_render::render(
            &mesh,
            &RenderOptions {
                view,
                ..Default::default()
            },
        )?;
        let name = format!("{:?}.png", view).to_ascii_lowercase();
        fs::write(output.join(&name), rendered.png).map_err(|e| e.to_string())?;
        let m = rendered.metadata;
        results.push(json!({"file":name,"metadata":{"view":format!("{:?}",m.view).to_ascii_lowercase(),"projection":m.projection,"width":m.width,"height":m.height,"framing_center":m.framing_center,"units_per_pixel":m.units_per_pixel,"margin_fraction":m.margin_fraction,"shading":m.shading,"depth_test":m.depth_test}}));
    }
    fs::write(output.join("views.json"), serde_json::to_vec_pretty(&json!({
        "runtime_manifest": manifest.file_name().map(|s|s.to_string_lossy().to_string()), "triangle_count": mesh.positions.len()/3, "views": results
    })).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    println!("Rendered seven actual OCCT views to {}", output.display());
    Ok(())
}
