//! Technical indicator kernels — Rust-owned numeric authority (constitution
//! §1: Market/Data processing / Numerical calculations / Performance-critical).
//!
//! Pure math: indicators accept price slices, return computed values. NaN/Inf
//! inputs fail safe (return None or NaN-free output). No market orchestration,
//! no Bar timestamps — callers own the windowing and alignment.

mod atr;
mod ema;
mod rsi;
mod sma;
mod vwap;

pub use atr::atr;
pub use ema::ema;
pub use rsi::rsi;
pub use sma::sma;
pub use vwap::vwap;
