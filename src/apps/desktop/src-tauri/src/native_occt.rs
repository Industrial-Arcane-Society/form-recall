//! Runtime-loaded OCCT C ABI. No C++ objects or allocations cross ownership.
use libloading::Library;
use std::{
    ffi::{CStr, c_char, c_void},
    marker::PhantomData,
    path::Path,
    ptr::NonNull,
    rc::Rc,
    sync::Arc,
};

type Handle = *mut c_void;
type Out = *mut Handle;
type Status = i32;
#[repr(C)]
#[derive(Default, Debug, Clone, Copy)]
pub struct Bounds {
    pub xmin: f64,
    pub ymin: f64,
    pub zmin: f64,
    pub xmax: f64,
    pub ymax: f64,
    pub zmax: f64,
}
#[repr(C)]
#[derive(Default, Debug, Clone, Copy)]
pub struct Analysis {
    pub valid: i32,
    pub solid_count: usize,
    pub closed_solid_count: usize,
}
#[repr(C)]
#[derive(Default)]
struct RawMesh {
    xyz: *mut f64,
    normals: *mut f64,
    vertex_count: usize,
}
#[derive(Debug)]
pub struct TriangleMesh {
    pub positions: Vec<[f64; 3]>,
    pub normals: Vec<[f64; 3]>,
}

struct Api {
    _library: Library,
    error: unsafe extern "C" fn() -> *const c_char,
    version: unsafe extern "C" fn() -> *const c_char,
    release: unsafe extern "C" fn(Handle),
    make_box: unsafe extern "C" fn(f64, f64, f64, f64, f64, f64, Out) -> Status,
    cylinder: unsafe extern "C" fn(f64, f64, f64, f64, f64, f64, f64, f64, Out) -> Status,
    sphere: unsafe extern "C" fn(f64, f64, f64, f64, Out) -> Status,
    fuse: unsafe extern "C" fn(*const Handle, usize, Out) -> Status,
    cut: unsafe extern "C" fn(Handle, *const Handle, usize, Out) -> Status,
    transform: unsafe extern "C" fn(Handle, *const f64, Out) -> Status,
    bounds: unsafe extern "C" fn(Handle, *mut Bounds) -> Status,
    analyze: unsafe extern "C" fn(Handle, *mut Analysis) -> Status,
    volume: unsafe extern "C" fn(Handle, *mut f64) -> Status,
    tessellate: unsafe extern "C" fn(Handle, f64, f64, *mut RawMesh) -> Status,
    mesh_release: unsafe extern "C" fn(*mut RawMesh),
}
#[derive(Clone)]
pub struct Kernel {
    api: Arc<Api>,
}
/// Shapes stay on their creating thread; OCCT tessellation mutates their caches.
pub struct Shape {
    api: Arc<Api>,
    handle: NonNull<c_void>,
    _thread: PhantomData<Rc<()>>,
}
impl Drop for Shape {
    fn drop(&mut self) {
        unsafe { (self.api.release)(self.handle.as_ptr()) }
    }
}

impl Kernel {
    pub fn load_manifest(manifest: &Path) -> Result<Self, String> {
        let path = crate::native_occt_runtime::verified_bridge_path(manifest)?;
        let kernel = Self::load(&path)?;
        if kernel.version() != "7.9.3" {
            return Err("OCCT runtime 返回版本不匹配；要求 7.9.3。".into());
        }
        Ok(kernel)
    }
    fn load(path: &Path) -> Result<Self, String> {
        let path = path
            .canonicalize()
            .map_err(|e| format!("OCCT bridge 不存在：{e}"))?;
        // Resolve dependent OCCT DLLs beside this absolute DLL or in the OS
        // default search directories; never the process working directory.
        #[cfg(windows)]
        let library: Library = unsafe {
            libloading::os::windows::Library::load_with_flags(
                &path,
                libloading::os::windows::LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
                    | libloading::os::windows::LOAD_LIBRARY_SEARCH_DEFAULT_DIRS,
            )
            .map_err(|e| format!("无法加载 OCCT bridge：{e}"))?
            .into()
        };
        #[cfg(not(windows))]
        let library =
            unsafe { Library::new(&path).map_err(|e| format!("无法加载 OCCT bridge：{e}"))? };
        macro_rules! sym {
            ($name:literal) => {
                unsafe {
                    *library
                        .get(concat!($name, "\0").as_bytes())
                        .map_err(|e| format!("OCCT bridge ABI 缺少 {}：{e}", $name))?
                }
            };
        }
        let abi: unsafe extern "C" fn() -> u32 = sym!("occt_bridge_abi_version");
        if unsafe { abi() } != 1 {
            return Err("OCCT bridge ABI 版本不支持；要求版本 1。".into());
        }
        let api = Api {
            error: sym!("occt_last_error"),
            version: sym!("occt_version"),
            release: sym!("occt_shape_release"),
            make_box: sym!("occt_make_box"),
            cylinder: sym!("occt_make_cylinder"),
            sphere: sym!("occt_make_sphere"),
            fuse: sym!("occt_fuse"),
            cut: sym!("occt_cut"),
            transform: sym!("occt_transform"),
            bounds: sym!("occt_shape_bounds"),
            analyze: sym!("occt_shape_analyze"),
            volume: sym!("occt_shape_volume"),
            tessellate: sym!("occt_shape_tessellate"),
            mesh_release: sym!("occt_mesh_release"),
            _library: library,
        };
        Ok(Self { api: Arc::new(api) })
    }
    pub fn version(&self) -> String {
        let p = unsafe { (self.api.version)() };
        if p.is_null() {
            "unknown".into()
        } else {
            unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned()
        }
    }
    fn create(&self, call: impl FnOnce(Out) -> Status) -> Result<Shape, String> {
        let mut out = std::ptr::null_mut();
        let status = call(&mut out);
        if let Err(error) = self.api.check(status) {
            if !out.is_null() {
                unsafe { (self.api.release)(out) }
            }
            return Err(error);
        }
        Ok(Shape {
            api: self.api.clone(),
            handle: NonNull::new(out).ok_or("OCCT 返回空形状句柄。")?,
            _thread: PhantomData,
        })
    }
    pub fn make_box(&self, size: [f64; 3], center: [f64; 3]) -> Result<Shape, String> {
        self.create(|out| unsafe {
            (self.api.make_box)(
                size[0], size[1], size[2], center[0], center[1], center[2], out,
            )
        })
    }
    pub fn cylinder(
        &self,
        radius: f64,
        height: f64,
        base: [f64; 3],
        axis: [f64; 3],
    ) -> Result<Shape, String> {
        self.create(|out| unsafe {
            (self.api.cylinder)(
                radius, height, base[0], base[1], base[2], axis[0], axis[1], axis[2], out,
            )
        })
    }
    pub fn sphere(&self, radius: f64, center: [f64; 3]) -> Result<Shape, String> {
        self.create(|out| unsafe {
            (self.api.sphere)(radius, center[0], center[1], center[2], out)
        })
    }
    fn handles(&self, shapes: &[&Shape]) -> Result<Vec<Handle>, String> {
        shapes
            .iter()
            .map(|s| {
                if Arc::ptr_eq(&s.api, &self.api) {
                    Ok(s.handle.as_ptr())
                } else {
                    Err("形状来自不同 OCCT bridge 实例。".into())
                }
            })
            .collect()
    }
    pub fn fuse(&self, shapes: &[&Shape]) -> Result<Shape, String> {
        if shapes.is_empty() {
            return Err("合并至少需要一个形状。".into());
        }
        let handles = self.handles(shapes)?;
        self.create(|out| unsafe { (self.api.fuse)(handles.as_ptr(), handles.len(), out) })
    }
    pub fn cut(&self, base: &Shape, cutters: &[&Shape]) -> Result<Shape, String> {
        self.handles(&[base])?;
        let handles = self.handles(cutters)?;
        self.create(|out| unsafe {
            (self.api.cut)(base.handle.as_ptr(), handles.as_ptr(), handles.len(), out)
        })
    }
    pub fn transform(&self, shape: &Shape, matrix: [f64; 16]) -> Result<Shape, String> {
        self.handles(&[shape])?;
        self.create(|out| unsafe {
            (self.api.transform)(shape.handle.as_ptr(), matrix.as_ptr(), out)
        })
    }
}
impl Api {
    fn check(&self, status: Status) -> Result<(), String> {
        if status == 0 {
            return Ok(());
        }
        let ptr = unsafe { (self.error)() };
        let text = if ptr.is_null() {
            "unknown kernel error".into()
        } else {
            unsafe { CStr::from_ptr(ptr) }
                .to_string_lossy()
                .into_owned()
        };
        Err(format!("OCCT error {status}: {text}"))
    }
}
impl Shape {
    pub fn bounds(&self) -> Result<Bounds, String> {
        let mut value = Bounds::default();
        self.api
            .check(unsafe { (self.api.bounds)(self.handle.as_ptr(), &mut value) })?;
        let b = [
            value.xmin, value.ymin, value.zmin, value.xmax, value.ymax, value.zmax,
        ];
        if b.iter().any(|v| !v.is_finite())
            || value.xmax < value.xmin
            || value.ymax < value.ymin
            || value.zmax < value.zmin
        {
            return Err("OCCT 返回无效包围盒。".into());
        }
        Ok(value)
    }
    pub fn analyze(&self) -> Result<Analysis, String> {
        let mut value = Analysis::default();
        self.api
            .check(unsafe { (self.api.analyze)(self.handle.as_ptr(), &mut value) })?;
        Ok(value)
    }
    pub fn volume(&self) -> Result<f64, String> {
        let mut value = 0.;
        self.api
            .check(unsafe { (self.api.volume)(self.handle.as_ptr(), &mut value) })?;
        if !value.is_finite() {
            return Err("OCCT 返回非有限体积。".into());
        }
        Ok(value)
    }
    pub fn tessellate(&self, linear: f64, angular: f64) -> Result<TriangleMesh, String> {
        let mut raw = RawMesh::default();
        let status =
            unsafe { (self.api.tessellate)(self.handle.as_ptr(), linear, angular, &mut raw) };
        // Guard every exit after the ABI call, including validation failures.
        struct MeshGuard<'a> {
            api: &'a Api,
            raw: RawMesh,
        }
        impl Drop for MeshGuard<'_> {
            fn drop(&mut self) {
                unsafe { (self.api.mesh_release)(&mut self.raw) }
            }
        }
        let guard = MeshGuard {
            api: &self.api,
            raw,
        };
        self.api.check(status)?;
        let raw = &guard.raw;
        if raw.vertex_count == 0
            || raw.vertex_count % 3 != 0
            || raw.vertex_count > 15_000_000
            || raw.xyz.is_null()
            || raw.normals.is_null()
        {
            return Err("OCCT 返回无效或过大的三角网格。".into());
        }
        let n = raw
            .vertex_count
            .checked_mul(3)
            .ok_or("三角网格大小溢出。")?;
        let points = unsafe { std::slice::from_raw_parts(raw.xyz, n) };
        let normals = unsafe { std::slice::from_raw_parts(raw.normals, n) };
        if points.iter().chain(normals.iter()).any(|v| !v.is_finite()) {
            return Err("三角网格存在非有限数据。".into());
        }
        Ok(TriangleMesh {
            positions: points.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect(),
            normals: normals
                .chunks_exact(3)
                .map(|v| [v[0], v[1], v[2]])
                .collect(),
        })
    }
}
