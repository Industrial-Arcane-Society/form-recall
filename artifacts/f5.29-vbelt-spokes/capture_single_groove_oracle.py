"""Capture the old Quick C300 one-groove spoked V-belt pulley oracle."""
from __future__ import annotations
import hashlib, json, platform, sys
from pathlib import Path
PROMPT = "V带轮 基准直径300 槽数1 槽型C 内孔20 宽度180"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = REPO / "src" / "legacy"
if str(LEGACY) not in sys.path: sys.path.insert(0, str(LEGACY))
from ai_modeler.core.export.exporters import export_stl
from ai_modeler.core.parsing.nlparser import Parser
from ai_modeler.parts import std_pulley

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()
def jv(x):
    if isinstance(x,dict): return {str(k):jv(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)): return [jv(v) for v in x]
    if x is None or isinstance(x,(str,int,float,bool)): return x
    raise TypeError(type(x).__name__)
parsed=Parser().parse(PROMPT)
doc=parsed.doc
polys=doc.build()
stats=doc.stats()
feature=doc.features[0]
params=dict(feature.params)
spec=std_pulley.spec(float(params['pitch_d']),int(params['grooves']),params['groove_type'],float(params['height']),float(params['bore_d']),keep_width=True)
profile=std_pulley.profile(spec)
stl=HERE/'legacy_document_build_raw_single_groove.stl'
export_stl(polys,str(stl),doc.name,binary=True)
brep_path=HERE/'legacy_document_build_brep_single_groove.brep'
try:
    shape=doc.build_brep()
    brep_report=jv(doc.brep_report)
    from ai_modeler.core.geometry import occ_kernel
    written=bool(shape is not None and occ_kernel.write_brep(shape,str(brep_path)))
    brep={'attempted':True,'available':shape is not None,'written':written,'report':brep_report,'path':brep_path.name if written else None,'bytes':brep_path.stat().st_size if written else None,'sha256':sha(brep_path) if written else None}
except Exception as exc:
    brep={'attempted':True,'available':False,'written':False,'error':{'type':type(exc).__name__,'message':str(exc)},'report':jv(doc.brep_report),'path':None,'bytes':None,'sha256':None}
report={
 'evidence_kind':'legacy_python_quick_vbelt_c300_single_groove_oracle',
 'oracle_scope':'Parser.parse -> Document.build -> binary STL; separate Document.build_brep attempt',
 'prompt':PROMPT,'prompt_sha256_utf8':hashlib.sha256(PROMPT.encode('utf-8')).hexdigest(),
 'parser_result':{'ok':parsed.ok,'recognized':parsed.recognized,'message':parsed.message,'steps':jv(parsed.steps),'warnings':jv(parsed.warnings),'questions':jv(parsed.questions),'assumed':jv(parsed.assumed)},
 'document_feature':jv(vars(feature)),
 'standard_profile_from_actual_feature_parameters':{'spec':jv(spec),'profile_points_radius_axis_mm':jv(profile)},
 'document_build_stats':jv(stats),
 'raw_mesh':{'path':stl.name,'format':'binary STL from legacy Document.build polygons','triangle_count':stats['triangles'],'face_count':stats['faces'],'bbox':jv(stats['bbox']),'size':jv(stats['size']),'legacy_mesh_volume_scalar_mm3':stats['volume'],'volume_semantics':'Legacy Document.stats polygon mesh scalar; separate from B-Rep volume.','bytes':stl.stat().st_size,'sha256':sha(stl)},
 'brep':brep,
 'runtime':{'python':platform.python_version(),'implementation':platform.python_implementation()},
 'limitations':['Old Parser/Document outputs are runtime-observed.','Raw mesh and B-Rep are distinct geometry paths; their volumes are reported separately.','No Rust Quick, CADPlan validator, Cargo, CATIA, or UI was run or validated.']
}
out=HERE/'single_groove_legacy_oracle.json'
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'json':str(out.relative_to(REPO)),'json_sha256':sha(out),'stl':str(stl.relative_to(REPO)),'stl_sha256':report['raw_mesh']['sha256'],'raw_stats':report['document_build_stats'],'feature_params':params,'spec':{'width':spec['width'],'spokes':spec['spokes'],'web':spec['web'],'hub_d':spec['hub_d'],'hub_len':spec['hub_len'],'web_s':spec['web_s'],'rim_id':spec['rim_id']},'brep':brep},ensure_ascii=False,indent=2))
