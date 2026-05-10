1. **Analyze Baileys components**: We need to port Baileys into Rust. I will look through the reference implementation (`References/Baileys/src`) and figure out the best way to port components incrementally. The first target is to implement JID utility functions in Rust (`jid-utils.ts`).
2. **Implement `wabinary/jid_utils` in Rust**: Create `wassupweb/src/wabinary/jid_utils.rs` with `jid_encode`, `jid_decode`, and other JID manipulation functions.
3. **Expose `wabinary/jid_utils` to Python and Node.js**: Use `pyo3` and `napi` to expose these Rust functions with ABI bindings. Make sure they are identical to their TS/Python counterparts.
4. **Implement `wabinary/constants`**: Port the byte constants from `wabinary/constants.ts` to Rust (`wassupweb/src/wabinary/constants.rs`).
5. **Implement `wabinary/types`**: Port binary node structures to Rust.
6. **Write tests**: I will use Cargo tests for Rust functions, and later run Python tests against the Python bindings.
7. **Pre-commit**: `pre_commit_instructions` will be called.
8. **Submit**.
