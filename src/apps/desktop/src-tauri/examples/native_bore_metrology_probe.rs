//! CLI B-Rep cylinder measurement probe; deterministic synthetic OCCT shapes only.
use ai_modeler_ui_lib::{native_bore_metrology, native_geometry_evidence::HoleRequest, native_occt::Kernel};
use serde_json::json;
use std::{env, fs, path::PathBuf};

fn request(x:f64,y:f64,radius:f64)->HoleRequest {
    HoleRequest { origin:[x,y,-5.0], axis:[0.0,0.0,1.0], radius,
        tmin:0.0,tmax:30.0,tolerance:1e-6 }
}
fn cut_hole(kernel:&Kernel,stock:&ai_modeler_ui_lib::native_occt::Shape,x:f64,y:f64,r:f64,z:f64,h:f64)
    ->Result<ai_modeler_ui_lib::native_occt::Shape,String> {
    let tool=kernel.cylinder(r,h,[x,y,z],[0.0,0.0,1.0])?;
    kernel.cut(stock,&[&tool])
}
fn main()->Result<(),String> {
    let args:Vec<_>=env::args().skip(1).collect();
    let manifest=PathBuf::from(args.first().ok_or("usage: native_bore_metrology_probe RUNTIME_MANIFEST OUTPUT_JSON")?);
    let output=PathBuf::from(args.get(1).ok_or("missing report output path")?);
    let kernel=Kernel::load_manifest(&manifest)?;
    let stock=kernel.make_box([100.0,80.0,20.0],[0.0,0.0,10.0])?;
    let xs=[-30.0,0.0,30.0]; let ys=[-25.0,0.0,25.0]; let radii=[5.0,3.0,5.0];
    let mut tools=Vec::new();
    for y in ys { for (col,x) in xs.into_iter().enumerate() {
        tools.push(kernel.cylinder(radii[col],20.0,[x,y,0.0],[0.0,0.0,1.0])?);
    }}
    let refs:Vec<_>=tools.iter().collect(); let nine=kernel.cut(&stock,&refs)?;
    let mut nine_reports=Vec::new();
    for y in ys { for (col,x) in xs.into_iter().enumerate() {
        let report=native_bore_metrology::measure_bore(&stock,&nine,&request(x,y,radii[col]),None)?;
        if report["accepted"]!=true { return Err(format!("9-hole measurement failed at ({x},{y}): {report}")); }
        let measured=report["actual_radius_measurements"].as_array().ok_or("missing radius measurements")?;
        if measured.iter().any(|r| (r.as_f64().unwrap_or(f64::NAN)-radii[col]).abs()>1e-6) {
            return Err(format!("incorrect analytic radius at ({x},{y}): {report}"));
        }
        nine_reports.push(json!({"center":[x,y],"requested_radius":radii[col],"measurement":report}));
    }}
    let blind=cut_hole(&kernel,&stock,15.0,10.0,4.0,10.0,10.0)?;
    let blind_report=native_bore_metrology::measure_bore(&stock,&blind,&request(15.0,10.0,4.0),Some(10.0))?;
    if blind_report["accepted"]!=true || blind_report["pre_post_material_evidence"]["summary"]["center_remaining_material"]!=true {
        return Err(format!("blind local span evidence failed: {blind_report}"));
    }
    let blind_wrong_depth=native_bore_metrology::measure_bore(&stock,&blind,&request(15.0,10.0,4.0),Some(8.0))?;
    if blind_wrong_depth["accepted"]==true {
        return Err(format!("blind-hole wrong-depth negative fixture was accepted: {blind_wrong_depth}"));
    }
    let misplaced=cut_hole(&kernel,&stock,5.0,0.0,5.0,0.0,20.0)?;
    let misplaced_report=native_bore_metrology::measure_bore(&stock,&misplaced,&request(0.0,0.0,5.0),None)?;
    if misplaced_report["accepted"]==true { return Err(format!("misplaced hole falsely matched: {misplaced_report}")); }
    let wrong_diameter=cut_hole(&kernel,&stock,0.0,0.0,4.0,0.0,20.0)?;
    let wrong_diameter_report=native_bore_metrology::measure_bore(&stock,&wrong_diameter,&request(0.0,0.0,5.0),None)?;
    if wrong_diameter_report["accepted"]==true { return Err(format!("wrong diameter falsely matched: {wrong_diameter_report}")); }
    let exterior_tool=kernel.cylinder(5.0,20.0,[0.0,0.0,0.0],[0.0,0.0,1.0])?;
    let exterior=kernel.fuse(&[&stock,&exterior_tool])?;
    let exterior_report=native_bore_metrology::measure_bore(&exterior,&exterior,&request(0.0,0.0,5.0),None)?;
    if exterior_report["accepted"]==true { return Err(format!("outer cylinder falsely counted as a bore: {exterior_report}")); }
    let report=json!({
        "status":"passed","occt_version":kernel.version(),"fixture_source":"synthetic_occt_brep_cli",
        "user_image_loaded":false,"agent_plan_injected":false,
        "measurement_method":"GeomAbs_Cylinder BRepAdaptor_Surface exact radius and trimmed UV; axis/normal orientation check; pre/post material intervals",
        "nine_through_holes":nine_reports,"blind_local_span":blind_report,"blind_wrong_depth_negative":blind_wrong_depth,
        "misplaced_hole_negative":misplaced_report,"wrong_diameter_negative":wrong_diameter_report,
        "external_cylinder_negative":exterior_report,
        "scope":"analytic face radius and axes are B-Rep measured; trimmed face rows can split a physical bore and are not bore counts; partial angular coverage remains explicitly reported"
    });
    let text=serde_json::to_string_pretty(&report).map_err(|e|e.to_string())?;
    if let Some(parent)=output.parent(){fs::create_dir_all(parent).map_err(|e|e.to_string())?;}
    fs::write(&output,text.as_bytes()).map_err(|e|e.to_string())?;
    println!("passed: 9 analytic hole measurements; blind, misplaced, wrong-diameter, external-cylinder cases checked; {}",output.display());
    Ok(())
}
