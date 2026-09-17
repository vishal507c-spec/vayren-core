fn main() {
    slint_build::compile("ui/app.slint").unwrap();
    // Headless responsive harness (tests only): a separate compilation unit
    // so the harness Window gets Rust bindings without touching the app
    // entry. Included from lib.rs under cfg(test) only.
    let manifest = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let out =
        std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("research_harness.rs");
    println!("cargo:rerun-if-changed=ui/research_harness.slint");
    println!("cargo:rerun-if-changed=ui/research.slint");
    println!("cargo:rerun-if-changed=ui/components.slint");
    println!("cargo:rerun-if-changed=ui/palette.slint");
    slint_build::compile_with_output_path(
        manifest.join("ui/research_harness.slint"),
        &out,
        slint_build::CompilerConfiguration::new(),
    )
    .unwrap();
    // LIVE harness: same test-only separate compilation unit.
    let live_out =
        std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("live_harness.rs");
    println!("cargo:rerun-if-changed=ui/live_harness.slint");
    println!("cargo:rerun-if-changed=ui/live.slint");
    slint_build::compile_with_output_path(
        manifest.join("ui/live_harness.slint"),
        &live_out,
        slint_build::CompilerConfiguration::new(),
    )
    .unwrap();
}
