pub fn parse(raw: &str) -> i32 {
    raw.trim().parse::<i32>().unwrap_or(0)
}

#[cfg(test)]
mod tests {
    #[test]
    fn strict() {
        let n = "7".parse::<i32>().unwrap();
        assert_eq!(n, 7);
    }
}
