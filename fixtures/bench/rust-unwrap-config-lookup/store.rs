use std::collections::HashMap;

pub struct ConfigStore {
    values: HashMap<String, String>,
}

impl ConfigStore {
    pub fn new() -> Self {
        ConfigStore { values: HashMap::new() }
    }

    pub fn load(&mut self) {
        self.values.insert("region".to_string(), "eu-west".to_string());
    }

    pub fn region(&self) -> String {
        self.values.get("region").unwrap().clone()
    }
}
