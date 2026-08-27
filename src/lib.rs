use pyo3::prelude::*;

#[pyfunction]
fn hello() -> &'static str {
    "Hello from financial-extractor-core Rust module!"
}

#[pymodule]
fn financial_extractor_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(hello, m)?)?;
    Ok(())
}
