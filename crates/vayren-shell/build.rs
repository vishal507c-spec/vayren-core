use std::path::{Path, PathBuf};

fn is_stale(inputs: &[PathBuf], output: &Path) -> bool {
    let Ok(out_meta) = std::fs::metadata(output) else {
        return true;
    };
    let Ok(out_mtime) = out_meta.modified() else {
        return true;
    };
    for input in inputs {
        if let Ok(in_meta) = std::fs::metadata(input) {
            if let Ok(in_mtime) = in_meta.modified() {
                if in_mtime > out_mtime {
                    return true;
                }
            }
        }
    }
    false
}

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let out_dir = PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let build_script = manifest.join("build.rs");
    let app_out = out_dir.join("app.rs");

    // Gather all ui/*.slint files and emit rerun-if-changed
    let mut slint_files = Vec::new();
    if let Ok(entries) = std::fs::read_dir(manifest.join("ui")) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|ext| ext.to_str()) == Some("slint") {
                if let Ok(rel) = path.strip_prefix(&manifest) {
                    println!("cargo:rerun-if-changed={}", rel.display());
                }
                slint_files.push(path);
            }
        }
    }
    slint_files.push(build_script.clone());

    if is_stale(&slint_files, &app_out) {
        slint_build::compile("ui/app.slint").unwrap();
    }

    // Headless responsive harness (tests only): a separate compilation unit
    // so the harness Window gets Rust bindings without touching the app
    // entry. Included from lib.rs under cfg(test) only.
    let out = out_dir.join("research_harness.rs");
    println!("cargo:rerun-if-changed=ui/research_harness.slint");
    println!("cargo:rerun-if-changed=ui/research.slint");
    println!("cargo:rerun-if-changed=ui/components.slint");
    println!("cargo:rerun-if-changed=ui/palette.slint");

    let research_inputs = [
        manifest.join("ui/research_harness.slint"),
        manifest.join("ui/research.slint"),
        manifest.join("ui/components.slint"),
        manifest.join("ui/palette.slint"),
        build_script.clone(),
    ];
    if is_stale(&research_inputs, &out) {
        slint_build::compile_with_output_path(
            manifest.join("ui/research_harness.slint"),
            &out,
            slint_build::CompilerConfiguration::new(),
        )
        .unwrap();
    }

    // LIVE harness: same test-only separate compilation unit.
    let live_out = PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("live_harness.rs");
    println!("cargo:rerun-if-changed=ui/live_harness.slint");
    println!("cargo:rerun-if-changed=ui/live.slint");
    println!("cargo:rerun-if-changed=ui/components.slint");
    println!("cargo:rerun-if-changed=ui/palette.slint");

    let live_inputs = [
        manifest.join("ui/live_harness.slint"),
        manifest.join("ui/live.slint"),
        manifest.join("ui/components.slint"),
        manifest.join("ui/palette.slint"),
        build_script,
    ];
    if is_stale(&live_inputs, &live_out) {
        slint_build::compile_with_output_path(
            manifest.join("ui/live_harness.slint"),
            &live_out,
            slint_build::CompilerConfiguration::new(),
        )
        .unwrap();
    }
}
