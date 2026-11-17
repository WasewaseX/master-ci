pub struct Gauge {
    value: f64,
}

impl Gauge {
    pub fn from_text(raw: &str) -> Gauge {
        let parsed: f64 = raw.parse().unwrap();
        Gauge { value: parsed }
    }

    pub fn read(&self) -> f64 {
        self.value
    }
}
