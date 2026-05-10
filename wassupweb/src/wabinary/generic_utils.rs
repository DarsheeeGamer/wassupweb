use std::collections::HashMap;

use super::types::{BinaryNode, BinaryNodeContent};

pub fn get_binary_node_children<'a>(node: Option<&'a BinaryNode>, child_tag: &str) -> Vec<&'a BinaryNode> {
    if let Some(node) = node {
        if let Some(BinaryNodeContent::Nodes(children)) = &node.content {
            return children.iter().filter(|c| c.tag == child_tag).collect();
        }
    }
    Vec::new()
}

pub fn get_binary_node_child<'a>(node: Option<&'a BinaryNode>, child_tag: &str) -> Option<&'a BinaryNode> {
    get_binary_node_children(node, child_tag).into_iter().next()
}

pub fn get_all_binary_node_children(node: &BinaryNode) -> Vec<&BinaryNode> {
    if let Some(BinaryNodeContent::Nodes(children)) = &node.content {
        children.iter().collect()
    } else {
        Vec::new()
    }
}

pub fn get_binary_node_child_buffer(node: Option<&BinaryNode>, child_tag: &str) -> Option<Vec<u8>> {
    let child = get_binary_node_child(node, child_tag)?;
    match &child.content {
        Some(BinaryNodeContent::Bytes(bytes)) => Some(bytes.clone()),
        _ => None,
    }
}

pub fn get_binary_node_child_string(node: Option<&BinaryNode>, child_tag: &str) -> Option<String> {
    let child = get_binary_node_child(node, child_tag)?;
    match &child.content {
        Some(BinaryNodeContent::Text(text)) => Some(text.clone()),
        Some(BinaryNodeContent::Bytes(bytes)) => String::from_utf8(bytes.clone()).ok(),
        _ => None,
    }
}

pub fn get_binary_node_child_uint(node: Option<&BinaryNode>, child_tag: &str, length: usize) -> Option<u64> {
    let buff = get_binary_node_child_buffer(node, child_tag)?;
    Some(buffer_to_uint(&buff, length))
}

pub fn assert_node_error_free(node: &BinaryNode) -> Result<(), String> {
    if let Some(err_node) = get_binary_node_child(Some(node), "error") {
        let text = err_node.attrs.get("text").cloned().unwrap_or_else(|| "Unknown error".to_string());
        let code = err_node.attrs.get("code").cloned().unwrap_or_default();
        return Err(format!("{} (code={})", text, code));
    }
    Ok(())
}

pub fn reduce_binary_node_to_dictionary(node: &BinaryNode, tag: &str) -> HashMap<String, String> {
    let nodes = get_binary_node_children(Some(node), tag);
    let mut result = HashMap::new();

    for current in nodes {
        if let Some(name) = current.attrs.get("name") {
            let val = current.attrs.get("value").or_else(|| current.attrs.get("config_value")).cloned().unwrap_or_default();
            result.insert(name.clone(), val);
        } else if let Some(key) = current.attrs.get("config_code") {
            let val = current.attrs.get("value").or_else(|| current.attrs.get("config_value")).cloned().unwrap_or_default();
            result.insert(key.clone(), val);
        }
    }
    result
}

fn buffer_to_uint(data: &[u8], length: usize) -> u64 {
    let mut value: u64 = 0;
    let len = data.len().min(length);
    for i in 0..len {
        value = 256 * value + (data[i] as u64);
    }
    value
}

pub fn binary_node_to_string(node: Option<&BinaryNode>, indent: usize) -> String {
    if let Some(n) = node {
        let tabs = "\t".repeat(indent);

        let mut attrs_text = String::new();
        for (k, v) in &n.attrs {
            if !attrs_text.is_empty() {
                attrs_text.push(' ');
            }
            attrs_text.push_str(&format!("{}='{}'", k, v));
        }

        let tag_start = if attrs_text.is_empty() {
            format!("<{}", n.tag)
        } else {
            format!("<{} {}", n.tag, attrs_text)
        };

        let children = match &n.content {
            Some(BinaryNodeContent::Text(text)) => format!("{}{}", "\t".repeat(indent + 1), text),
            Some(BinaryNodeContent::Bytes(bytes)) => format!("{}{}", "\t".repeat(indent + 1), hex::encode(bytes)),
            Some(BinaryNodeContent::Nodes(nodes)) => {
                nodes.iter()
                    .map(|child| binary_node_to_string(Some(child), indent + 1))
                    .collect::<Vec<_>>()
                    .join("\n")
            },
            None => "".to_string(),
        };

        if !children.is_empty() {
            format!("{}>\n{}\n{}</{}>", tag_start, children, tabs, n.tag)
        } else {
            format!("{}/>", tag_start)
        }
    } else {
        "".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_binary_node_children() {
        let child = BinaryNode {
            tag: "child".to_string(),
            attrs: HashMap::new(),
            content: None,
        };
        let node = BinaryNode {
            tag: "parent".to_string(),
            attrs: HashMap::new(),
            content: Some(BinaryNodeContent::Nodes(vec![child.clone()])),
        };

        let children = get_binary_node_children(Some(&node), "child");
        assert_eq!(children.len(), 1);
        assert_eq!(children[0].tag, "child");

        let none_children = get_binary_node_children(Some(&node), "missing");
        assert_eq!(none_children.len(), 0);
    }

    #[test]
    fn test_get_binary_node_child_buffer() {
        let child = BinaryNode {
            tag: "child".to_string(),
            attrs: HashMap::new(),
            content: Some(BinaryNodeContent::Bytes(vec![1, 2, 3])),
        };
        let node = BinaryNode {
            tag: "parent".to_string(),
            attrs: HashMap::new(),
            content: Some(BinaryNodeContent::Nodes(vec![child])),
        };

        let buf = get_binary_node_child_buffer(Some(&node), "child");
        assert_eq!(buf, Some(vec![1, 2, 3]));
    }

    #[test]
    fn test_buffer_to_uint() {
        assert_eq!(buffer_to_uint(&[1, 2], 2), 258);
    }
}
