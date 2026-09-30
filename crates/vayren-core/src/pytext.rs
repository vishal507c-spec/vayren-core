//! Python-repr-compatible text formatting for kernel-owned reason strings.
//!
//! Several readiness reasons interpolate a value with `!r` (for example
//! `environment mismatch: credentials say 'sandbox', expected 'live'`). The
//! wording is part of the contract those gates publish, so the quoting rule
//! moves with it. ASCII behaves exactly like CPython; non-ASCII characters
//! are passed through unescaped (CPython additionally escapes a handful of
//! Unicode control/format code points, which no reason string in this
//! codebase has ever contained) — the same documented divergence as
//! `kill_switch`'s timestamp rendering.

/// Render `text` the way Python's `repr(str)` would, quotes included.
pub fn py_repr(text: &str) -> String {
    let has_single = text.contains('\'');
    let has_double = text.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c == '\x7f' => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_text_takes_single_quotes() {
        assert_eq!(py_repr("live"), "'live'");
        assert_eq!(py_repr(""), "''");
        assert_eq!(py_repr("sandbox account"), "'sandbox account'");
    }

    #[test]
    fn an_apostrophe_flips_the_quote_char_like_python_does() {
        assert_eq!(py_repr("it's"), "\"it's\"");
        // Both quote kinds present: CPython keeps single quotes and escapes the inner one.
        assert_eq!(py_repr("a\"b'c"), "'a\"b\\'c'");
    }

    #[test]
    fn control_characters_and_backslashes_are_escaped() {
        assert_eq!(py_repr("a\\b"), "'a\\\\b'");
        assert_eq!(py_repr("a\tb\r"), "'a\\tb\\r'");
        assert_eq!(py_repr("a\nb"), "'a\\nb'");
        assert_eq!(py_repr("\x00"), "'\\x00'");
        assert_eq!(py_repr("\x7f"), "'\\x7f'");
    }

    #[test]
    fn non_ascii_passes_through() {
        assert_eq!(py_repr("नमस्ते"), "'नमस्ते'");
    }
}
