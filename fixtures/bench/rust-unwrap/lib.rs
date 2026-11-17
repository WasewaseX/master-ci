pub fn parse(raw: &str) -> i32 {
    let n = raw.trim().parse::<i32>().unwrap();
    n
}
