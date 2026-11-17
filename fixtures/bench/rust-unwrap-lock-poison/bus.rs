use std::sync::{Mutex, MutexGuard};

pub struct EventBus {
    state: Mutex<u32>,
}

impl EventBus {
    pub fn new() -> Self {
        EventBus { state: Mutex::new(0) }
    }

    pub fn bump(&self) -> u32 {
        let mut guard: MutexGuard<u32> = self.state.lock().unwrap();
        *guard += 1;
        *guard
    }
}
