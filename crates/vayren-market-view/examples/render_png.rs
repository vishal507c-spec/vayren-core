//! Offscreen PNG render of a Market snapshot through the real C ABI.
//!
//! Visual-verification harness: feeds a captured backend snapshot JSON into
//! the same `create → set_snapshot → render` path the embed host uses and
//! writes the frame as a PNG. Run:
//! `cargo run -p vayren-market-view --example render_png -- <snap.json> <out.png> [w] [h]`
//!
//! The PNG encoder is dependency-free (stored deflate blocks) — this crate
//! deliberately carries no image dependency.

use std::ffi::CString;
use std::io::Write;

fn crc32(data: &[u8]) -> u32 {
    let mut table = [0u32; 256];
    for (i, entry) in table.iter_mut().enumerate() {
        let mut c = i as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 {
                0xEDB8_8320 ^ (c >> 1)
            } else {
                c >> 1
            };
        }
        *entry = c;
    }
    let mut c = 0xFFFF_FFFFu32;
    for &b in data {
        c = table[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8);
    }
    c ^ 0xFFFF_FFFF
}

fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(kind);
    out.extend_from_slice(data);
    let mut body = kind.to_vec();
    body.extend_from_slice(data);
    out.extend_from_slice(&crc32(&body).to_be_bytes());
}

fn adler32(data: &[u8]) -> u32 {
    let (mut a, mut b) = (1u32, 0u32);
    for &byte in data {
        a = (a + byte as u32) % 65521;
        b = (b + a) % 65521;
    }
    (b << 16) | a
}

/// RGB8 → PNG (color type 2, stored/uncompressed deflate blocks).
fn write_png(path: &str, rgb: &[u8], w: u32, h: u32) {
    let stride = w as usize * 3;
    let mut raw = Vec::with_capacity((stride + 1) * h as usize);
    for y in 0..h as usize {
        raw.push(0u8); // filter: none
        raw.extend_from_slice(&rgb[y * stride..(y + 1) * stride]);
    }
    // zlib stream with stored deflate blocks (max 65535 bytes each).
    let mut z = vec![0x78u8, 0x01];
    let blocks: Vec<&[u8]> = raw.chunks(65535).collect();
    for (i, block) in blocks.iter().enumerate() {
        let last = i == blocks.len() - 1;
        z.push(if last { 1 } else { 0 });
        z.extend_from_slice(&(block.len() as u16).to_le_bytes());
        z.extend_from_slice(&(!block.len() as u16).to_le_bytes());
        z.extend_from_slice(block);
    }
    z.extend_from_slice(&adler32(&raw).to_be_bytes());

    let mut out = vec![0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A];
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&w.to_be_bytes());
    ihdr.extend_from_slice(&h.to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
    chunk(&mut out, b"IHDR", &ihdr);
    chunk(&mut out, b"IDAT", &z);
    chunk(&mut out, b"IEND", &[]);
    std::fs::File::create(path)
        .unwrap()
        .write_all(&out)
        .unwrap();
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 2 {
        eprintln!("usage: render_png <snapshot.json> <out.png> [width] [height]");
        std::process::exit(2);
    }
    let w: u32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(1600);
    let h: u32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(900);
    let json = std::fs::read_to_string(&args[0]).expect("snapshot json");
    let payload = CString::new(json).unwrap();

    let view = vayren_market_view::vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null(), "view create");
    assert_eq!(
        vayren_market_view::vayren_market_view_set_snapshot(view, payload.as_ptr()),
        0
    );
    assert_eq!(
        vayren_market_view::vayren_market_view_resize(view, w, h, 1.0),
        0
    );
    assert_eq!(vayren_market_view::vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view::vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );
    write_png(&args[1], &out, w, h);
    vayren_market_view::vayren_market_view_destroy(view);
    println!("wrote {} ({w}x{h})", args[1]);
}
