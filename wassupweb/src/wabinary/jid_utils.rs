
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WAJIDDomains {
    Whatsapp = 0,
    Lid = 1,
    Hosted = 128,
    HostedLid = 129,
}

impl WAJIDDomains {
    pub fn from_u32(value: u32) -> Option<Self> {
        match value {
            0 => Some(WAJIDDomains::Whatsapp),
            1 => Some(WAJIDDomains::Lid),
            128 => Some(WAJIDDomains::Hosted),
            129 => Some(WAJIDDomains::HostedLid),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullJid {
    pub user: String,
    pub server: String,
    pub device: Option<u32>,
    pub domain_type: u32,
}

pub fn get_server_from_domain_type(initial_server: &str, domain_type: Option<WAJIDDomains>) -> &str {
    match domain_type {
        Some(WAJIDDomains::Lid) => "lid",
        Some(WAJIDDomains::Hosted) => "hosted",
        Some(WAJIDDomains::HostedLid) => "hosted.lid",
        _ => initial_server,
    }
}

pub fn jid_encode(
    user: Option<&str>,
    server: &str,
    device: Option<u32>,
    agent: Option<u32>,
) -> String {
    let mut result = String::new();
    if let Some(u) = user {
        result.push_str(u);
    }
    if let Some(a) = agent {
        result.push_str(&format!("_{}", a));
    }
    if let Some(d) = device {
        result.push_str(&format!(":{}", d));
    }
    result.push('@');
    result.push_str(server);
    result
}

pub fn jid_decode(jid: Option<&str>) -> Option<FullJid> {
    let jid = jid?;
    let sep_idx = jid.find('@')?;
    let (user_combined, server) = jid.split_at(sep_idx);
    let server = &server[1..]; // skip '@'

    let mut parts = user_combined.split(':');
    let user_agent = parts.next().unwrap_or("");
    let device_str = parts.next();
    let device = device_str.and_then(|s| s.parse::<u32>().ok());

    let mut user_parts = user_agent.split('_');
    let user = user_parts.next().unwrap_or("").to_string();
    let agent_str = user_parts.next();
    let agent = agent_str.and_then(|s| s.parse::<u32>().ok());

    let mut domain_type = WAJIDDomains::Whatsapp as u32;
    if server == "lid" {
        domain_type = WAJIDDomains::Lid as u32;
    } else if server == "hosted" {
        domain_type = WAJIDDomains::Hosted as u32;
    } else if server == "hosted.lid" {
        domain_type = WAJIDDomains::HostedLid as u32;
    } else if let Some(a) = agent {
        domain_type = a;
    }

    Some(FullJid {
        user,
        server: server.to_string(),
        device,
        domain_type,
    })
}

pub fn are_jids_same_user(jid1: Option<&str>, jid2: Option<&str>) -> bool {
    let j1 = jid_decode(jid1);
    let j2 = jid_decode(jid2);
    match (j1, j2) {
        (Some(j1), Some(j2)) => j1.user == j2.user,
        _ => false,
    }
}

pub fn is_jid_meta_ai(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@bot"))
}

pub fn is_pn_user(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@s.whatsapp.net"))
}

pub fn is_lid_user(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@lid"))
}

pub fn is_jid_broadcast(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@broadcast"))
}

pub fn is_jid_group(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@g.us"))
}

pub fn is_jid_status_broadcast(jid: &str) -> bool {
    jid == "status@broadcast"
}

pub fn is_jid_newsletter(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@newsletter"))
}

pub fn is_hosted_pn_user(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@hosted"))
}

pub fn is_hosted_lid_user(jid: Option<&str>) -> bool {
    jid.map_or(false, |s| s.ends_with("@hosted.lid"))
}

pub fn is_jid_bot(jid: Option<&str>) -> bool {
    if let Some(j) = jid {
        if !j.ends_with("@c.us") {
            return false;
        }
        if let Some((user, _)) = j.split_once('@') {
            return (user.len() == 11 && user.starts_with("1313555")) || (user.len() == 11 && user.starts_with("131655500"));
        }
    }
    false
}

pub fn jid_normalized_user(jid: Option<&str>) -> String {
    if let Some(result) = jid_decode(jid) {
        let server = if result.server == "c.us" {
            "s.whatsapp.net"
        } else {
            &result.server
        };
        jid_encode(Some(&result.user), server, None, None)
    } else {
        String::new()
    }
}

pub fn transfer_device(from_jid: &str, to_jid: &str) -> Result<String, String> {
    let from_decoded = jid_decode(Some(from_jid));
    let to_decoded = jid_decode(Some(to_jid)).ok_or_else(|| format!("invalid JID: {}", to_jid))?;

    let device_id = from_decoded.and_then(|d| d.device).unwrap_or(0);
    Ok(jid_encode(Some(&to_decoded.user), &to_decoded.server, (device_id > 0).then_some(device_id), None))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_jid_encode() {
        assert_eq!(
            jid_encode(Some("123"), "s.whatsapp.net", None, None),
            "123@s.whatsapp.net"
        );
        assert_eq!(
            jid_encode(Some("123"), "s.whatsapp.net", Some(1), None),
            "123:1@s.whatsapp.net"
        );
        assert_eq!(
            jid_encode(Some("123"), "s.whatsapp.net", Some(1), Some(2)),
            "123_2:1@s.whatsapp.net"
        );
        assert_eq!(
            jid_encode(None, "server@c.us", None, None),
            "@server@c.us"
        );
    }

    #[test]
    fn test_jid_decode() {
        let jid = jid_decode(Some("123@s.whatsapp.net")).unwrap();
        assert_eq!(jid.user, "123");
        assert_eq!(jid.server, "s.whatsapp.net");
        assert_eq!(jid.device, None);
        assert_eq!(jid.domain_type, WAJIDDomains::Whatsapp as u32);

        let jid2 = jid_decode(Some("123:1@s.whatsapp.net")).unwrap();
        assert_eq!(jid2.user, "123");
        assert_eq!(jid2.server, "s.whatsapp.net");
        assert_eq!(jid2.device, Some(1));

        let jid3 = jid_decode(Some("123_2:1@s.whatsapp.net")).unwrap();
        assert_eq!(jid3.user, "123");
        assert_eq!(jid3.server, "s.whatsapp.net");
        assert_eq!(jid3.device, Some(1));
        assert_eq!(jid3.domain_type, 2);

        let jid_lid = jid_decode(Some("123@lid")).unwrap();
        assert_eq!(jid_lid.domain_type, WAJIDDomains::Lid as u32);

        let jid_hosted = jid_decode(Some("123@hosted")).unwrap();
        assert_eq!(jid_hosted.domain_type, WAJIDDomains::Hosted as u32);

        assert!(jid_decode(Some("invalid")).is_none());
        assert!(jid_decode(None).is_none());
    }

    #[test]
    fn test_is_functions() {
        assert!(is_pn_user(Some("123@s.whatsapp.net")));
        assert!(!is_pn_user(Some("123@g.us")));

        assert!(is_lid_user(Some("123@lid")));
        assert!(!is_lid_user(Some("123@s.whatsapp.net")));

        assert!(is_jid_group(Some("123@g.us")));

        assert!(is_jid_bot(Some("13135551234@c.us")));
        assert!(!is_jid_bot(Some("123@c.us")));
    }

    #[test]
    fn test_are_jids_same_user() {
        assert!(are_jids_same_user(Some("123:1@s.whatsapp.net"), Some("123:2@s.whatsapp.net")));
        assert!(!are_jids_same_user(Some("123@s.whatsapp.net"), Some("456@s.whatsapp.net")));
        assert!(!are_jids_same_user(None, Some("123@s.whatsapp.net")));
    }

    #[test]
    fn test_jid_normalized_user() {
        assert_eq!(jid_normalized_user(Some("123:1@c.us")), "123@s.whatsapp.net");
        assert_eq!(jid_normalized_user(Some("123@lid")), "123@lid");
        assert_eq!(jid_normalized_user(None), "");
    }

    #[test]
    fn test_transfer_device() {
        assert_eq!(transfer_device("123:1@s.whatsapp.net", "456@s.whatsapp.net").unwrap(), "456:1@s.whatsapp.net");
        assert_eq!(transfer_device("123@s.whatsapp.net", "456:2@s.whatsapp.net").unwrap(), "456@s.whatsapp.net");
    }
}
