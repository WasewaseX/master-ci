pub struct Pipeline {
    steps: usize,
}

impl Pipeline {
    pub fn staged() -> Pipeline {
        let steps = std::env::var("PIPELINE_STAGES").unwrap().len();
        Pipeline { steps: steps }
    }

    pub fn steps(&self) -> usize {
        self.steps
    }
}

#[cfg(test)]
mod tests {
    use super::Pipeline;

    #[test]
    fn reports_steps() {
        let pipeline = Pipeline { steps: 3 };
        let count = Some(pipeline.steps()).unwrap();
        assert_eq!(count, 3);
    }
}
