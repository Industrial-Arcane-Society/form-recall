// Package viewport displays CAD meshes in an interactive native OpenGL surface.
//
// Construct a Viewport once and call SetWindow after creating its MyGo window.
// View supplies a flex layout element. Load prepares self-contained glTF 2 GLB
// meshes on a worker and applies the newest result through Window.Update. All
// other Viewport methods run on the MyGo UI thread. The package-level LoadGLB
// and DecodeGLB functions can also be used independently from worker goroutines.
//
// On Windows, the renderer owns a child HWND, WGL context, depth buffer and
// retained GPU display lists. It redraws for input, layout changes and camera
// transitions. Animation timers stop once the camera settles or the view hides.
// Left drag orbits, Shift-left/right/middle drag pans, the wheel zooms, F and
// double-click fit, and keys 1 through 7 select the six orthographic directions
// and the isometric direction. Projection remains a separate option.
//
// Child HWNDs appear above MyGo-drawn content. Call SetVisible(false) while a
// settings overlay or overlapping GPU popup is shown. MyGo MenuButton uses the
// platform popup menu and can appear above this child. Select, ComboBox and
// PopoverMenuButton use drawn panels and require the same overlap handling.
//
// Capture returns the actual OpenGL framebuffer. CaptureWindow combines that
// framebuffer with MyGo's content capture at its physical pixel bounds. Call it
// on the UI thread after the layout has been presented. CapturePage alone does
// not include the OpenGL child window.
//
// The CAD loader supports indexed and expanded triangle meshes, scene node
// transforms, smooth normals and material base colors. Faceted CAD normals are
// blended across shallow triangle joins while sharp edges retain their normals.
// The orientation triad stays in the lower-left corner and follows the camera.
// Textures, skinning,
// animation, sparse accessors, Draco compression and external buffers are not
// implemented. Invalid geometry and unsupported compressed inputs return errors.
package viewport
