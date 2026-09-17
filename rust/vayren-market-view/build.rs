fn main() {
    // The host window re-exports MarketScreen verbatim; Slint's own dependency
    // tracking misses the cross-crate import, so declare it explicitly: any
    // change to the verified screen (or the shared design/component files it
    // uses) must regenerate the host bindings.
    println!("cargo:rerun-if-changed=../vayren-shell/ui/market.slint");
    println!("cargo:rerun-if-changed=../vayren-shell/ui/palette.slint");
    println!("cargo:rerun-if-changed=../vayren-shell/ui/components.slint");
    slint_build::compile("ui/market_host.slint").unwrap();
}
