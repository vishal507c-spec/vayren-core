use std::path::{Path, PathBuf};

// FNV-1a 64: deterministic across processes (unlike DefaultHasher, whose
// SipHash keys are random per instance), so the fingerprint is stable
// between cargo invocations. std-only: a hashing crate here would slow
// every CI cold build that compiles this script.
fn fnv1a(bytes: &[u8], mut hash: u64) -> u64 {
    const PRIME: u64 = 0x00000100000001B3;
    for byte in bytes {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

// Content fingerprint of every input (path + bytes). mtimes lie after a
// fresh checkout — every source file looks newer than the cached codegen,
// forcing a full Slint recompile on every CI run. Content does not.
// `None` = unreadable input, always recompile (fail-open, never stale).
fn content_hash(inputs: &[PathBuf]) -> Option<u64> {
    let mut hash = 0xCBF29CE484222325u64;
    for input in inputs {
        hash = fnv1a(input.to_string_lossy().as_bytes(), hash);
        hash = fnv1a(&[0], hash);
        hash = fnv1a(&std::fs::read(input).ok()?, hash);
        hash = fnv1a(&[0], hash);
    }
    Some(hash)
}

fn sidecar(output: &Path) -> PathBuf {
    output.with_extension("hash")
}

// True when the generated file exists and its inputs are byte-identical to
// the last successful compilation. The sidecar is written only after
// `slint_build` succeeds, so a failed compile always retries next run.
fn up_to_date(output: &Path, inputs: &[PathBuf]) -> bool {
    let (Some(hash), Ok(recorded)) = (
        content_hash(inputs),
        std::fs::read_to_string(sidecar(output)),
    ) else {
        return false;
    };
    output.exists() && recorded.trim() == hash.to_string()
}

fn record(output: &Path, inputs: &[PathBuf]) {
    if let Some(hash) = content_hash(inputs) {
        let _ = std::fs::write(sidecar(output), hash.to_string());
    }
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
    // `read_dir` order is OS-dependent: sort so the fingerprint cannot flip
    // between runs on identical content.
    slint_files.sort();

    if up_to_date(&app_out, &slint_files) {
        // Skipped compile must be OBSERVABLE-identical to a fresh one:
        // `slint_build::compile` emits this rustc-env pointing at the
        // generated module (`slint::include_modules!()` reads it at compile
        // time), so skipping the codegen without emitting it breaks the
        // crate build — the exact symptom this line prevents.
        println!(
            "cargo:rustc-env=SLINT_INCLUDE_GENERATED={}",
            app_out.display()
        );
    } else {
        slint_build::compile("ui/app.slint").unwrap();
        record(&app_out, &slint_files);
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
    if !up_to_date(&out, &research_inputs) {
        slint_build::compile_with_output_path(
            manifest.join("ui/research_harness.slint"),
            &out,
            slint_build::CompilerConfiguration::new(),
        )
        .unwrap();
        record(&out, &research_inputs);
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
    if !up_to_date(&live_out, &live_inputs) {
        slint_build::compile_with_output_path(
            manifest.join("ui/live_harness.slint"),
            &live_out,
            slint_build::CompilerConfiguration::new(),
        )
        .unwrap();
        record(&live_out, &live_inputs);
    }
}
