//! Actual OCCT parity and visible-thread probe for native standard features.
use ai_modeler_ui_lib::{
    native_occt::{Kernel, Shape},
    native_render::{self, RenderOptions, View},
    native_standard_features,
};
use serde_json::{Value, json};
use std::{env, fs, path::PathBuf};

fn geometry(shape: &Shape) -> Result<Value, String> {
    let bounds = shape.bounds()?;
    let analysis = shape.analyze()?;
    Ok(json!({
        "valid": analysis.valid,
        "solid_count": analysis.solid_count,
        "closed_solid_count": analysis.closed_solid_count,
        "volume_mm3": shape.volume()?,
        "bbox": [bounds.xmin,bounds.ymin,bounds.zmin,bounds.xmax,bounds.ymax,bounds.zmax]
    }))
}

fn main() -> Result<(), String> {
    let args: Vec<_> = env::args().skip(1).collect();
    let manifest = PathBuf::from(
        args.first()
            .ok_or("usage: native_standard_features_probe RUNTIME_MANIFEST OUTPUT_DIR")?,
    );
    let output_dir = PathBuf::from(args.get(1).ok_or("missing output directory")?);
    fs::create_dir_all(&output_dir).map_err(|e| e.to_string())?;
    let kernel = Kernel::load_manifest(&manifest)?;

    let (gears, gear_support) = native_standard_features::tools(
        &kernel,
        "gear",
        &json!({"teeth":20,"module":2.0,"height":10.0,"center":[0.0,0.0,0.0],"axis":"Z","base":false}),
    )?;
    if gears.len() != 1 {
        return Err("gear builder did not return exactly one solid".into());
    }
    let gear_analysis = gears[0].analyze()?;
    if gear_analysis.valid == 0
        || gear_analysis.solid_count != 1
        || gear_analysis.closed_solid_count != 1
    {
        return Err("gear is not one valid closed solid".into());
    }

    let (bolts, bolt_support) = native_standard_features::tools(
        &kernel,
        "socket_head_bolt",
        &json!({"shank_d":8.0,"thread_pitch":1.25,"length":20.0,"head_d":13.0,"head_h":8.0,"hex_s":6.0,"hex_t":4.0,"segments":48,"center":[0.0,0.0,0.0]}),
    )?;
    if bolts.len() != 1 {
        return Err("socket_head_bolt builder did not return exactly one solid".into());
    }
    let bolt_mesh = bolts[0].tessellate(0.08, 0.35)?;
    let mut rendered = Vec::new();
    for view in [View::Front, View::Iso, View::Right] {
        let image = native_render::render(
            &bolt_mesh,
            &RenderOptions {
                view,
                width: 900,
                height: 700,
                ..Default::default()
            },
        )?;
        let name = format!("socket-head-bolt-{:?}.png", view).to_lowercase();
        fs::write(output_dir.join(&name), image.png).map_err(|e| e.to_string())?;
        rendered.push(json!({"file":name,"view":format!("{:?}",view),"projection":image.metadata.projection,"shading":image.metadata.shading}));
    }
    let report = json!({
        "occt_version":kernel.version(),
        "gear":{"support":gear_support,"geometry":geometry(&gears[0])?},
        "socket_head_bolt":{"support":bolt_support,"geometry":geometry(&bolts[0])?,"tessellated_triangles":bolt_mesh.positions.len()/3,"rendered_views":rendered}
    });
    fs::write(
        output_dir.join("native-standard-features-report.json"),
        serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    println!("{}", report);
    Ok(())
}
