pub mod wabinary;

#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "nodejs")]
use napi_derive::napi;

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
#[cfg_attr(feature = "python", pyo3(signature = (user, server, device=None, agent=None)))]
pub fn jid_encode(
    user: Option<String>,
    server: String,
    device: Option<u32>,
    agent: Option<u32>,
) -> String {
    wabinary::jid_utils::jid_encode(user.as_deref(), &server, device, agent)
}

#[cfg_attr(feature = "nodejs", napi(object))]
#[cfg(feature = "nodejs")]
pub struct NodeFullJid {
    pub user: String,
    pub server: String,
    pub device: Option<u32>,
    pub domain_type: u32,
}

#[cfg(feature = "python")]
#[pyclass(get_all, set_all, dict)]
#[derive(Clone, Debug)]
pub struct PyFullJid {
    pub user: String,
    pub server: String,
    pub device: Option<u32>,
    pub domain_type: u32,
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn are_jids_same_user(jid1: Option<String>, jid2: Option<String>) -> bool {
    wabinary::jid_utils::are_jids_same_user(jid1.as_deref(), jid2.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_jid_meta_ai(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_jid_meta_ai(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_pn_user(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_pn_user(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_lid_user(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_lid_user(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_jid_broadcast(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_jid_broadcast(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_jid_group(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_jid_group(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_jid_status_broadcast(jid: String) -> bool {
    wabinary::jid_utils::is_jid_status_broadcast(&jid)
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_jid_newsletter(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_jid_newsletter(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_hosted_pn_user(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_hosted_pn_user(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_hosted_lid_user(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_hosted_lid_user(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn is_jid_bot(jid: Option<String>) -> bool {
    wabinary::jid_utils::is_jid_bot(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn jid_normalized_user(jid: Option<String>) -> String {
    wabinary::jid_utils::jid_normalized_user(jid.as_deref())
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg(feature = "nodejs")]
pub fn transfer_device(from_jid: String, to_jid: String) -> napi::Result<String> {
    wabinary::jid_utils::transfer_device(&from_jid, &to_jid)
        .map_err(|e| napi::Error::new(napi::Status::GenericFailure, e))
}

#[cfg(feature = "python")]
#[pyfunction]
#[pyo3(name = "transfer_device")]
pub fn transfer_device_py(from_jid: String, to_jid: String) -> PyResult<String> {
    wabinary::jid_utils::transfer_device(&from_jid, &to_jid)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
}

#[cfg_attr(feature = "nodejs", napi)]
#[cfg(feature = "nodejs")]
pub fn jid_decode(jid: Option<String>) -> Option<NodeFullJid> {
    wabinary::jid_utils::jid_decode(jid.as_deref()).map(|j| NodeFullJid {
        user: j.user,
        server: j.server,
        device: j.device,
        domain_type: j.domain_type,
    })
}

#[cfg(feature = "python")]
#[pyfunction]
#[pyo3(name = "jid_decode")]
pub fn jid_decode_py(jid: Option<String>) -> Option<PyFullJid> {
    wabinary::jid_utils::jid_decode(jid.as_deref()).map(|j| PyFullJid {
        user: j.user,
        server: j.server,
        device: j.device,
        domain_type: j.domain_type,
    })
}

#[cfg(feature = "python")]
#[pymodule]
fn wassupweb(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(jid_encode, m)?)?;
    m.add_function(wrap_pyfunction!(jid_decode_py, m)?)?;
    m.add_function(wrap_pyfunction!(are_jids_same_user, m)?)?;
    m.add_function(wrap_pyfunction!(is_jid_meta_ai, m)?)?;
    m.add_function(wrap_pyfunction!(is_pn_user, m)?)?;
    m.add_function(wrap_pyfunction!(is_lid_user, m)?)?;
    m.add_function(wrap_pyfunction!(is_jid_broadcast, m)?)?;
    m.add_function(wrap_pyfunction!(is_jid_group, m)?)?;
    m.add_function(wrap_pyfunction!(is_jid_status_broadcast, m)?)?;
    m.add_function(wrap_pyfunction!(is_jid_newsletter, m)?)?;
    m.add_function(wrap_pyfunction!(is_hosted_pn_user, m)?)?;
    m.add_function(wrap_pyfunction!(is_hosted_lid_user, m)?)?;
    m.add_function(wrap_pyfunction!(is_jid_bot, m)?)?;
    m.add_function(wrap_pyfunction!(jid_normalized_user, m)?)?;
    m.add_function(wrap_pyfunction!(transfer_device_py, m)?)?;
    m.add_class::<PyFullJid>()?;
    Ok(())
}
