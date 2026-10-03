//! Actual Rust -> C ABI -> OCCT geometry acceptance; no Python or LLM.
use ai_modeler_ui_lib::native_occt::{Kernel, Shape};
use serde_json::json;
use std::{env, fs, path::PathBuf, time::Instant};

fn main() -> Result<(), String> {
    let started = Instant::now();
    let args: Vec<_> = env::args().skip(1).collect();
    let manifest = PathBuf::from(
        args.first()
            .ok_or("usage: native_occt_probe RUNTIME_MANIFEST OUTPUT_JSON")?,
    );
    let output = PathBuf::from(args.get(1).ok_or("missing output JSON path")?);
    eprintln!("checkpoint: loading kernel");
    let kernel = Kernel::load_manifest(&manifest)?;
    eprintln!("checkpoint: primitives");
    if kernel.make_box([0., 2., 3.], [0.; 3]).is_ok() {
        return Err("invalid primitive dimensions accepted".into());
    }
    let left = kernel.make_box([10.; 3], [0.; 3])?;
    let right = kernel.make_box([10.; 3], [5., 0., 0.])?;
    let union = kernel.fuse(&[&left, &right])?;
    if (union.volume()? - 1500.).abs() > 1e-6 || union.analyze()?.solid_count != 1 {
        return Err("overlapping box fuse failed".into());
    }
    let sphere = kernel.sphere(5., [0.; 3])?;
    if (sphere.volume()? - 4. / 3. * std::f64::consts::PI * 125.).abs() > 1e-6 {
        return Err("sphere primitive volume failed".into());
    }
    let scaled = kernel.transform(
        &left,
        [
            2., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
        ],
    )?;
    if (scaled.volume()? - 2000.).abs() > 1e-6 {
        return Err("nonuniform affine scale failed".into());
    }
    if kernel.transform(&left, [0.; 16]).is_ok() {
        return Err("singular transform accepted".into());
    }
    let other_kernel = Kernel::load_manifest(&manifest)?;
    if other_kernel.fuse(&[&left]).is_ok() {
        return Err("foreign shape handle accepted".into());
    }
    let stock = kernel.make_box([100., 40., 120.], [0., 0., 60.])?;
    // Front is +Y. Central column retreats 8 mm, with all three vertical
    // rows using large / small / large diameters. Test dimensions are fixtures.
    let mut tools: Vec<Shape> = vec![kernel.make_box([20., 8., 124.], [0., 16., 60.])?];
    for x in [-32., 0., 32.] {
        for (z, radius) in [(22., 6.), (60., 4.), (98., 6.)] {
            tools.push(kernel.cylinder(radius, 44., [x, -22., z], [0., 1., 0.])?);
        }
    }
    let refs: Vec<_> = tools.iter().collect();
    eprintln!("checkpoint: boolean cut");
    let part = kernel.cut(&stock, &refs)?;
    eprintln!("checkpoint: B-Rep checks");
    let analysis = part.analyze()?;
    let bounds = part.bounds()?;
    let volume = part.volume()?;
    let expected = 100. * 40. * 120.
        - 20. * 8. * 120.
        - std::f64::consts::PI * (6. * 6. + 4. * 4. + 6. * 6.) * (40. * 2. + 32.);
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err("recessed nine-hole fixture is not a single valid closed solid".into());
    }
    let volume_error = (volume - expected).abs();
    if volume_error > 1e-5 {
        return Err(format!("volume difference {volume_error}"));
    }
    let dimensions = [
        bounds.xmax - bounds.xmin,
        bounds.ymax - bounds.ymin,
        bounds.zmax - bounds.zmin,
    ];
    if dimensions
        .iter()
        .zip([100., 40., 120.])
        .any(|(a, b)| (a - b).abs() > 1e-7)
    {
        return Err("fixture bounding box mismatch".into());
    }
    eprintln!("checkpoint: tessellation");
    let mesh = part.tessellate(0.1, 0.5)?;
    if mesh.positions.len() < 3 || mesh.positions.len() != mesh.normals.len() {
        return Err("empty or inconsistent triangle output".into());
    }
    // Translation returns an independent handle and preserves analytic volume.
    eprintln!("checkpoint: transform");
    let translated = kernel.transform(
        &part,
        [
            1., 0., 0., 10., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
        ],
    )?;
    if (translated.bounds()?.xmin - bounds.xmin - 10.).abs() > 1e-7
        || (translated.volume()? - volume).abs() > 1e-5
    {
        return Err("shape transform failed".into());
    }
    let rotated = kernel.transform(
        &part,
        [
            0., -1., 0., 0., 1., 0., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
        ],
    )?;
    let rotated_bounds = rotated.bounds()?;
    if (rotated_bounds.xmax - rotated_bounds.xmin - 40.).abs() > 1e-7
        || (rotated_bounds.ymax - rotated_bounds.ymin - 100.).abs() > 1e-7
        || (rotated.volume()? - volume).abs() > 1e-5
    {
        return Err("rigid rotation failed".into());
    }
    let report = json!({"status":"passed","occt_version":kernel.version(),"execution_engine":"rust_c_abi_occt",
        "python_used":false,"fixture":"front_y_central_recess_nine_holes_big_small_big_rows",
        "dimensions_mm":dimensions,"valid":analysis.valid==1,"solid_count":analysis.solid_count,
        "closed_solid_count":analysis.closed_solid_count,"volume_mm3":volume,"analytic_volume_mm3":expected,
        "volume_absolute_error_mm3":volume_error,"triangle_count":mesh.positions.len()/3,
        "invalid_argument_rejected":true,"translation_verified":true,"rotation_verified":true,
        "sphere_verified":true,"fuse_verified":true,"nonuniform_scale_verified":true,
        "foreign_handle_rejected":true,"runtime_manifest_verified":true,
        "duration_ms_before_handle_release":started.elapsed().as_millis(),
        "scope":"geometry_fixture_only_not_agent_image_reproduction"});
    let text = serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?;
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(output, text.as_bytes()).map_err(|e| e.to_string())?;
    println!("{text}");
    Ok(())
}
