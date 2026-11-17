pub struct Vault {
    secret: String,
}

impl Vault {
    pub fn open(raw: &str) -> Vault {
        let secret = raw.parse().expect("vault key must be valid utf-8");
        Vault { secret: secret }
    }

    pub fn reveal(&self) -> &str {
        &self.secret
    }
}
