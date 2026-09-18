//! Generic name-based registry.
//!
//! Minimal Rust port of 01_core/core/registry/registry.py. Full ComponentRegistry
//! orchestration (manifest/capability wiring) stays Python per constitution §11
//! (big-bang risk, stdlib-only foundation).

use std::collections::HashMap;
use std::sync::{Arc, RwLock};

/// Generic named registry for services.
///
/// Thread-safe string→value map with duplicate-key protection.
#[derive(Clone)]
pub struct Registry<T: Clone> {
    items: Arc<RwLock<HashMap<String, T>>>,
}

impl<T: Clone> Registry<T> {
    /// Create an empty registry.
    pub fn new() -> Self {
        Self {
            items: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Register an item. Panics if name already exists.
    pub fn register(&self, name: impl Into<String>, item: T) {
        let name = name.into();
        let mut items = self.items.write().unwrap();
        if items.contains_key(&name) {
            panic!("Item already registered: {}", name);
        }
        items.insert(name, item);
    }

    /// Get an item by name. Panics if not found.
    pub fn get(&self, name: &str) -> T {
        self.items
            .read()
            .unwrap()
            .get(name)
            .cloned()
            .unwrap_or_else(|| panic!("Item not found: {}", name))
    }

    /// Try to get an item by name.
    pub fn try_get(&self, name: &str) -> Option<T> {
        self.items.read().unwrap().get(name).cloned()
    }

    /// List all registered names, sorted.
    pub fn list(&self) -> Vec<String> {
        let items = self.items.read().unwrap();
        let mut names: Vec<_> = items.keys().cloned().collect();
        names.sort();
        names
    }

    /// Check if a name is registered.
    pub fn contains(&self, name: &str) -> bool {
        self.items.read().unwrap().contains_key(name)
    }

    /// Count of registered items.
    pub fn len(&self) -> usize {
        self.items.read().unwrap().len()
    }

    /// True if empty.
    pub fn is_empty(&self) -> bool {
        self.items.read().unwrap().is_empty()
    }
}

impl<T: Clone> Default for Registry<T> {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_and_get() {
        let reg = Registry::new();
        reg.register("loader", 42);
        assert_eq!(reg.get("loader"), 42);
    }

    #[test]
    #[should_panic(expected = "already registered")]
    fn duplicate_register() {
        let reg = Registry::new();
        reg.register("service", 1);
        reg.register("service", 2);
    }

    #[test]
    #[should_panic(expected = "not found")]
    fn missing_get() {
        let reg: Registry<i32> = Registry::new();
        reg.get("missing");
    }

    #[test]
    fn try_get() {
        let reg = Registry::new();
        assert_eq!(reg.try_get("missing"), None);
        reg.register("present", 99);
        assert_eq!(reg.try_get("present"), Some(99));
    }

    #[test]
    fn list_sorted() {
        let reg = Registry::new();
        reg.register("zebra", 1);
        reg.register("alpha", 2);
        reg.register("beta", 3);
        assert_eq!(reg.list(), vec!["alpha", "beta", "zebra"]);
    }

    #[test]
    fn contains_and_len() {
        let reg = Registry::new();
        assert!(!reg.contains("test"));
        assert_eq!(reg.len(), 0);

        reg.register("test", 42);
        assert!(reg.contains("test"));
        assert_eq!(reg.len(), 1);
    }
}
