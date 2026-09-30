//! Sandbox fill-script policy — the Rust-owned authority for `SandboxBroker`.
//!
//! `08_execution/execution/broker/sandbox.py` used to interpret its own
//! scripted policies order by order:
//!
//! | Python (retired here) | Rust (here) |
//! |---|---|
//! | `settle`'s `delay:` branch (parse, count down, expire) | `settle_decision` |
//! | `settle`'s `reject:` branch (reason or fallback) | `settle_decision` |
//! | `settle`'s `full` / `partial:<qty>` branches | `settle_decision` |
//! | `settle`'s unknown-policy fail-closed rejection | `settle_decision` |
//!
//! What stays Python is the venue itself: order records, state transitions,
//! events, and the simulated-fill call that turns a decision into a `Fill`.
//! A decision names *what* to do; the broker still performs it.
//!
//! The numeric text grammar follows CPython's `int()` / `float()` for ASCII
//! input: surrounding whitespace, an explicit sign, and PEP 515 underscores
//! between digits. [`py_int`] narrows one case on purpose and says so.

/// One settle decision for a scripted policy string.
#[derive(Debug, PartialEq)]
pub enum Settle {
    /// `delay:<n>` still counting down; the broker stores `next` and waits.
    Wait { next: String },
    /// Fill the order's remaining quantity.
    Full,
    /// Fill `qty` (the scripted `partial:<qty>` amount).
    Partial { qty: f64 },
    /// Reject the order with this reason.
    Reject { reason: String },
}

/// Kind vocabulary the bridge reads back; the strings are kernel-owned.
pub const KIND_WAIT: &str = "WAIT";
pub const KIND_FULL: &str = "FULL";
pub const KIND_PARTIAL: &str = "PARTIAL";
pub const KIND_REJECT: &str = "REJECT";

const DELAY_PREFIX: &str = "delay:";
const REJECT_PREFIX: &str = "reject:";
const PARTIAL_PREFIX: &str = "partial:";
const FULL_POLICY: &str = "full";

/// Interpret one scripted policy. Unknown text fails closed — the order is
/// rejected, never filled by accident.
pub fn settle_decision(policy: &str) -> Settle {
    if let Some(argument) = policy.strip_prefix(DELAY_PREFIX) {
        return match py_int(argument) {
            None => Settle::Reject {
                reason: format!("bad delay policy: {policy}"),
            },
            Some(remaining) if remaining > 0 => Settle::Wait {
                next: format!("{}{}", DELAY_PREFIX, remaining - 1),
            },
            Some(_) => Settle::Full,
        };
    }
    if let Some(argument) = policy.strip_prefix(REJECT_PREFIX) {
        let reason = if argument.is_empty() {
            "venue reject".to_string()
        } else {
            argument.to_string()
        };
        return Settle::Reject { reason };
    }
    if policy == FULL_POLICY {
        return Settle::Full;
    }
    if let Some(argument) = policy.strip_prefix(PARTIAL_PREFIX) {
        return match py_float(argument) {
            None => Settle::Reject {
                reason: format!("bad partial policy: {policy}"),
            },
            Some(qty) => Settle::Partial { qty },
        };
    }
    Settle::Reject {
        reason: format!("unknown policy: {policy}"),
    }
}

impl Settle {
    /// `(kind, text, number)` as the bridge receives it: `text` is the next
    /// policy for a wait and the reason for a rejection, `number` is the
    /// scripted quantity for a partial fill.
    pub fn parts(&self) -> (&'static str, String, Option<f64>) {
        match self {
            Settle::Wait { next } => (KIND_WAIT, next.clone(), None),
            Settle::Full => (KIND_FULL, String::new(), None),
            Settle::Partial { qty } => (KIND_PARTIAL, String::new(), Some(*qty)),
            Settle::Reject { reason } => (KIND_REJECT, reason.clone(), None),
        }
    }
}

/// CPython's `int(text)` for ASCII input: optional surrounding whitespace,
/// sign and decimal digits whose underscores must sit between digits.
///
/// Narrowed on purpose: a digit string too large for `i64` reads as invalid
/// (CPython would keep the arbitrary-precision value), so a pathological
/// `delay:` script rejects instead of stalling an order forever.
pub fn py_int(text: &str) -> Option<i64> {
    let (negative, body) = split_sign(trim(text))?;
    let taken = digit_run(body)?;
    if taken != body.len() {
        return None;
    }
    let digits: String = body.chars().filter(|char| *char != '_').collect();
    let value = digits.parse::<i64>().ok()?;
    Some(if negative { -value } else { value })
}

/// CPython's `float(text)` for ASCII input, including `inf`, `infinity` and
/// `nan` in any case. Rust's own parser is not trusted with the accepted
/// shape: the grammar is validated here, underscores are removed, and only
/// then is the canonical text handed to the standard parser.
pub fn py_float(text: &str) -> Option<f64> {
    let (negative, rest) = split_sign(trim(text))?;
    let lowered = rest.to_ascii_lowercase();
    if matches!(lowered.as_str(), "inf" | "infinity" | "nan") {
        return Some(match lowered.as_str() {
            "nan" => f64::NAN,
            _ if negative => f64::NEG_INFINITY,
            _ => f64::INFINITY,
        });
    }
    let value = decimal(rest)?.parse::<f64>().ok()?;
    Some(if negative && !value.is_nan() {
        -value
    } else {
        value
    })
}

/// Validate a Python float literal and re-emit it as a plain
/// `<digits>e<exponent>` string with the underscore separators removed, so
/// Rust's parser only ever sees the one shape it is trusted with.
fn decimal(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    let mut index = 0usize;

    let integer = digit_run(text).unwrap_or(0);
    index += integer;
    if bytes.get(index) == Some(&b'.') {
        index += 1;
        index += digit_run(&text[index..]).unwrap_or(0);
    }
    if index == 0 {
        return None;
    }
    let mantissa_end = index;

    let mut exponent: i64 = 0;
    if matches!(bytes.get(index), Some(b'e') | Some(b'E')) {
        index += 1;
        let tail = &text[index..];
        let sign_len = match tail.as_bytes().first() {
            Some(b'-') | Some(b'+') => 1,
            _ => 0,
        };
        let negative = tail.starts_with('-');
        let body = &tail[sign_len..];
        let taken = digit_run(body)?;
        if taken != body.len() {
            return None;
        }
        index += sign_len + taken;
        let magnitude = match body.replace('_', "").parse::<i64>() {
            Ok(value) => value,
            // An exponent this wide only asks for an overflow, which the
            // parser below answers with infinity — as CPython does.
            Err(_) => i64::MAX,
        };
        exponent = if negative { -magnitude } else { magnitude };
    }
    if index != text.len() {
        return None;
    }

    let mantissa = &text[..mantissa_end];
    let (int_part, frac_part) = match mantissa.split_once('.') {
        Some((head, tail)) => (head, tail),
        None => (mantissa, ""),
    };
    let int_digits = int_part.replace('_', "");
    let frac_digits = frac_part.replace('_', "");
    let all = format!("{int_digits}{frac_digits}");
    if all.is_empty() {
        return None;
    }
    let scale: i64 = i64::try_from(frac_digits.len()).unwrap_or(i64::MAX);
    let significant = all.trim_start_matches('0');
    let mantissa_digits = if significant.is_empty() {
        "0"
    } else {
        significant
    };
    Some(format!(
        "{mantissa_digits}e{}",
        exponent.saturating_sub(scale)
    ))
}

fn trim(text: &str) -> &str {
    text.trim_matches(is_ascii_space as fn(char) -> bool)
}

fn is_ascii_space(char: char) -> bool {
    char.is_ascii_whitespace()
}

fn split_sign(text: &str) -> Option<(bool, &str)> {
    match text.as_bytes().first() {
        Some(b'-') => Some((true, &text[1..])),
        Some(b'+') => Some((false, &text[1..])),
        _ => Some((false, text)),
    }
}

/// Length of a valid digit run: at least one digit, underscores only between
/// digits. `None` when the run cannot start or an underscore is dangling.
fn digit_run(text: &str) -> Option<usize> {
    let bytes = text.as_bytes();
    let mut index = 0usize;
    let mut started = false;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte.is_ascii_digit() {
            started = true;
            index += 1;
        } else if byte == b'_' {
            let before = started && bytes[index - 1].is_ascii_digit();
            let after = bytes.get(index + 1).is_some_and(u8::is_ascii_digit);
            if !before || !after {
                return None;
            }
            index += 1;
        } else {
            break;
        }
    }
    if started {
        Some(index)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reject(reason: &str) -> Settle {
        Settle::Reject {
            reason: reason.to_string(),
        }
    }

    #[test]
    fn unknown_text_never_fills() {
        assert_eq!(settle_decision(""), reject("unknown policy: "));
        assert_eq!(settle_decision("FULL"), reject("unknown policy: FULL"));
        assert_eq!(
            settle_decision("partial"),
            reject("unknown policy: partial")
        );
        assert_eq!(settle_decision("freeze"), reject("unknown policy: freeze"));
        assert_eq!(settle_decision("full"), Settle::Full);
    }

    #[test]
    fn delay_counts_down_and_then_expires_to_full() {
        assert_eq!(
            settle_decision("delay:3"),
            Settle::Wait {
                next: "delay:2".into()
            }
        );
        assert_eq!(
            settle_decision("delay:1"),
            Settle::Wait {
                next: "delay:0".into()
            }
        );
        assert_eq!(settle_decision("delay:0"), Settle::Full);
        assert_eq!(settle_decision("delay:-4"), Settle::Full);
        assert_eq!(
            settle_decision("delay: 2 "),
            Settle::Wait {
                next: "delay:1".into()
            }
        );
        assert_eq!(
            settle_decision("delay:1_0"),
            Settle::Wait {
                next: "delay:9".into()
            }
        );
        assert_eq!(
            settle_decision("delay:2.0"),
            reject("bad delay policy: delay:2.0")
        );
        assert_eq!(
            settle_decision("delay:"),
            reject("bad delay policy: delay:")
        );
        assert_eq!(
            settle_decision("delay:_1"),
            reject("bad delay policy: delay:_1")
        );
        assert_eq!(
            settle_decision("delay:1_"),
            reject("bad delay policy: delay:1_")
        );
        assert_eq!(
            settle_decision("delay:99999999999999999999999"),
            reject("bad delay policy: delay:99999999999999999999999")
        );
    }

    #[test]
    fn reject_reason_keeps_the_venue_text_after_the_colon() {
        assert_eq!(settle_decision("reject:"), reject("venue reject"));
        assert_eq!(settle_decision("reject:margin"), reject("margin"));
        assert_eq!(
            settle_decision("reject:no liquidity"),
            reject("no liquidity")
        );
        assert_eq!(settle_decision("reject::x"), reject(":x"));
    }

    #[test]
    fn partial_reads_the_scripted_quantity() {
        assert_eq!(settle_decision("partial:5"), Settle::Partial { qty: 5.0 });
        assert_eq!(settle_decision("partial:2.5"), Settle::Partial { qty: 2.5 });
        assert_eq!(
            settle_decision("partial: 1e3 "),
            Settle::Partial { qty: 1000.0 }
        );
        assert_eq!(settle_decision("partial:-1"), Settle::Partial { qty: -1.0 });
        assert_eq!(
            settle_decision("partial:1_0.5"),
            Settle::Partial { qty: 10.5 }
        );
        assert_eq!(settle_decision("partial:.5"), Settle::Partial { qty: 0.5 });
        assert_eq!(
            settle_decision("partial:1.e5"),
            Settle::Partial { qty: 100000.0 }
        );
        assert_eq!(
            settle_decision("partial:infinity"),
            Settle::Partial { qty: f64::INFINITY }
        );
        assert_eq!(
            settle_decision("partial:-inf"),
            Settle::Partial {
                qty: f64::NEG_INFINITY
            }
        );
        assert!(matches!(
            settle_decision("partial:nan"),
            Settle::Partial { qty } if qty.is_nan()
        ));
        assert_eq!(
            settle_decision("partial:1e"),
            reject("bad partial policy: partial:1e")
        );
        assert_eq!(
            settle_decision("partial:0x10"),
            reject("bad partial policy: partial:0x10")
        );
        assert_eq!(
            settle_decision("partial:1.2.3"),
            reject("bad partial policy: partial:1.2.3")
        );
        assert_eq!(
            settle_decision("partial:"),
            reject("bad partial policy: partial:")
        );
        assert_eq!(
            settle_decision("partial:--1"),
            reject("bad partial policy: partial:--1")
        );
    }

    #[test]
    fn parts_name_the_kind_for_the_bridge() {
        assert_eq!(
            Settle::Wait {
                next: "delay:1".into()
            }
            .parts(),
            (KIND_WAIT, "delay:1".to_string(), None)
        );
        assert_eq!(Settle::Full.parts(), (KIND_FULL, String::new(), None));
        assert_eq!(
            Settle::Partial { qty: 2.5 }.parts(),
            (KIND_PARTIAL, String::new(), Some(2.5))
        );
        assert_eq!(
            reject("venue reject").parts(),
            (KIND_REJECT, "venue reject".to_string(), None)
        );
    }

    #[test]
    fn integer_grammar_matches_python_on_the_edge_shapes() {
        assert_eq!(py_int(" 12 "), Some(12));
        assert_eq!(py_int("+12"), Some(12));
        assert_eq!(py_int("-12"), Some(-12));
        assert_eq!(py_int("\t\n 7\r"), Some(7));
        assert_eq!(py_int("1 2"), None);
        assert_eq!(py_int("12.0"), None);
        assert_eq!(py_int(""), None);
        assert_eq!(py_int("i64::MAX"), None);
        assert_eq!(py_int(&i64::MAX.to_string()), Some(i64::MAX));
        assert_eq!(py_int(&format!("-{}", i64::MAX)), Some(-i64::MAX));
    }

    #[test]
    fn float_grammar_matches_python_on_the_edge_shapes() {
        assert_eq!(py_float("0"), Some(0.0));
        assert!(py_float("-0.0") == Some(-0.0));
        assert_eq!(py_float("1e-7"), Some(1e-7));
        assert_eq!(py_float(" 1.5 "), Some(1.5));
        assert!(py_float("NAN").is_some_and(f64::is_nan));
        assert!(py_float("-nan").is_some_and(f64::is_nan));
        assert_eq!(py_float("Infinity"), Some(f64::INFINITY));
        assert_eq!(py_float("1e1_0"), Some(1e10));
        assert_eq!(py_float("_1"), None);
        assert_eq!(py_float("1_"), None);
        assert_eq!(py_float("."), None);
        assert_eq!(py_float("+"), None);
        assert_eq!(py_float("1 2"), None);
        assert_eq!(py_float("infi"), None);
        assert_eq!(py_float("1e2.5"), None);
        assert_eq!(py_float("1e+2"), Some(100.0));
        assert_eq!(py_float("0.0"), Some(0.0));
        assert_eq!(py_float("00012.500"), Some(12.5));
        assert_eq!(
            py_float("1.7976931348623157e309"),
            Some(f64::INFINITY),
            "overflow answers infinity, as CPython does"
        );
    }
}
