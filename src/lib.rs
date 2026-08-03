use pyo3::prelude::*;

#[pyfunction]
fn hello() -> &'static str {
    "Hello from bill-extractor-core Rust module!"
}

#[pymodule]
fn bill_extractor_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(hello, m)?)?;
    Ok(())
}
