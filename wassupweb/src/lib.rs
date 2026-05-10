#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "nodejs")]
use napi_derive::napi;

/// Adds two numbers.
#[cfg_attr(feature = "nodejs", napi)]
#[cfg_attr(feature = "python", pyfunction)]
pub fn add(a: u32, b: u32) -> u32 {
    a + b
}

/// A module for Python.
#[cfg(feature = "python")]
#[pymodule]
fn wassupweb(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(add, m)?)?;
    Ok(())
}
