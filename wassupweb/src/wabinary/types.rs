use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub enum BinaryNodeContent {
    Nodes(Vec<BinaryNode>),
    Text(String),
    Bytes(Vec<u8>),
}

#[derive(Debug, Clone, PartialEq)]
pub struct BinaryNode {
    pub tag: String,
    pub attrs: HashMap<String, String>,
    pub content: Option<BinaryNodeContent>,
}
