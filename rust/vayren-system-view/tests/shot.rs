//! Temporary visual-evidence shot (deleted after review): renders the broker
//! CONNECTION workspace offscreen and dumps raw RGB for PNG conversion.

use vayren_system_view::{
    vayren_system_view_create, vayren_system_view_destroy, vayren_system_view_render,
    vayren_system_view_set_snapshot, vayren_system_view_tick,
};

const W: usize = 1440;
const H: usize = 900;

const FYERS_READY: &str = r#"{
    "brokers": [
        {"id": "zerodha", "display_name": "Zerodha",
         "venue_subtitle": "Kite Connect", "status": "CONNECTED"},
        {"id": "fyers", "display_name": "Fyers",
         "venue_subtitle": "FYERS API v3", "status": "LOGIN_REQUIRED"}
    ],
    "selected_id": "fyers",
    "display_name": "Fyers",
    "venue_subtitle": "FYERS API v3",
    "environment": "paper",
    "status_raw": "LOGIN_REQUIRED",
    "configured": false,
    "can_login": false,
    "can_disconnect": false,
    "can_refresh": true,
    "reason": "",
    "credential_fields": [
        {"key": "app_id", "label": "App ID",
         "placeholder": "Enter FYERS App ID", "secret": false, "required": true},
        {"key": "secret", "label": "Secret",
         "placeholder": "Enter FYERS Secret ID", "secret": true, "required": true},
        {"key": "client_id", "label": "Client ID",
         "placeholder": "Enter your Client ID", "secret": false, "required": false},
        {"key": "totp_secret", "label": "TOTP Secret",
         "placeholder": "Enter TOTP Secret", "secret": true, "required": false},
        {"key": "pin", "label": "PIN",
         "placeholder": "Enter 4-digit PIN", "secret": true, "required": false}
    ],
    "checks": {},
    "capabilities": [],
    "blockers": []
}"#;

#[test]
fn workspace_shot_fyers_ready() {
    unsafe {
        let view = vayren_system_view_create(W as u32, H as u32, 1.0);
        assert!(!view.is_null());
        let c = std::ffi::CString::new(FYERS_READY).unwrap();
        assert_eq!(vayren_system_view_set_snapshot(view, c.as_ptr()), 0);
        assert_eq!(vayren_system_view_tick(view), 0);
        let mut buf = vec![0u8; W * H * 3];
        assert_eq!(
            vayren_system_view_render(view, buf.as_mut_ptr(), buf.len()),
            1
        );
        std::fs::write(
            r"C:\Users\visha\AppData\Local\Temp\opencode\conn_1440x900.rgb",
            &buf,
        )
        .unwrap();
        vayren_system_view_destroy(view);
    }
}
