//! Event bus — type-safe publish/subscribe with zero-cost static dispatch.
//!
//! Rust port of 01_core/core/event_bus/event_bus.py. Provides compile-time
//! type safety, no allocations in hot path, and panic-free handler execution.
//!
//! Python-parity mapping:
//! | Python (`event_bus.py`)                  | Rust (this module)                              |
//! |------------------------------------------|-----------------------------------------------|
//! | `subscribe(event_type, handler)`         | `subscribe::<E>(handler) -> HandlerId`        |
//! | `unsubscribe(event_type, handler)`       | `unsubscribe::<E>(id) -> bool`                |
//! | missing handler raises `ValueError`      | missing id returns `false` (no exceptions)    |
//! | `publish(event)`                         | `publish(event)`                              |
//! | `clear()`                                | `clear()`                                     |
//!
//! Rationale for the `HandlerId` difference: Rust closures (`Box<dyn Fn>`)
//! cannot be compared for identity, so `subscribe` hands out an opaque id
//! that `unsubscribe` uses as the key. Ordering (subscription order),
//! exact-type dispatch, and per-handler panic isolation match Python.

use std::any::{Any, TypeId};
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, RwLock,
};

/// Event marker trait. All events must implement this.
pub trait Event: Any + Send + Sync {}

/// Opaque key returned by [`EventBus::subscribe`], consumed by
/// [`EventBus::unsubscribe`]. Replaces Python's handler-identity removal.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct HandlerId(u64);

/// Type-erased handler function.
type BoxedHandler = Box<dyn Fn(&dyn Any) + Send + Sync>;

/// In-process publish/subscribe event bus.
///
/// Thread-safe, panic-isolating event dispatcher. Handlers run synchronously
/// on the publisher's thread in subscription order. Failed handlers are
/// logged but do not propagate.
#[derive(Clone)]
pub struct EventBus {
    subscribers: Arc<RwLock<HashMap<TypeId, Vec<(HandlerId, BoxedHandler)>>>>,
    next_id: Arc<AtomicU64>,
}

impl EventBus {
    /// Create a new empty event bus.
    pub fn new() -> Self {
        Self {
            subscribers: Arc::new(RwLock::new(HashMap::new())),
            next_id: Arc::new(AtomicU64::new(1)),
        }
    }

    /// Subscribe a typed handler to an event.
    ///
    /// The handler will be called synchronously whenever an event of type `E`
    /// is published. Handlers run in subscription order. Returns an opaque
    /// [`HandlerId`] for later removal via [`EventBus::unsubscribe`].
    pub fn subscribe<E, F>(&self, handler: F) -> HandlerId
    where
        E: Event + 'static,
        F: Fn(&E) + Send + Sync + 'static,
    {
        let id = HandlerId(self.next_id.fetch_add(1, Ordering::Relaxed));
        let type_id = TypeId::of::<E>();
        let boxed: BoxedHandler = Box::new(move |event: &dyn Any| {
            if let Some(typed) = event.downcast_ref::<E>() {
                handler(typed);
            }
        });

        let mut subs = self.subscribers.write().unwrap();
        subs.entry(type_id)
            .or_insert_with(Vec::new)
            .push((id, boxed));
        id
    }

    /// Remove a previously subscribed handler.
    ///
    /// Returns `true` when the id was present and removed, `false` when
    /// unknown. (Python raises `ValueError` for an unknown handler; Rust
    /// has no exceptions, so absence is a `false` return.)
    pub fn unsubscribe<E>(&self, id: HandlerId) -> bool
    where
        E: Event + 'static,
    {
        let type_id = TypeId::of::<E>();
        let mut subs = self.subscribers.write().unwrap();
        if let Some(handlers) = subs.get_mut(&type_id) {
            let before = handlers.len();
            handlers.retain(|(hid, _)| *hid != id);
            return handlers.len() != before;
        }
        false
    }

    /// Publish an event to all registered handlers.
    ///
    /// Handlers run synchronously in subscription order. If a handler panics,
    /// the panic is caught, logged, and does not affect other handlers.
    pub fn publish<E>(&self, event: E)
    where
        E: Event + 'static,
    {
        let type_id = TypeId::of::<E>();
        let subs = self.subscribers.read().unwrap();

        if let Some(handlers) = subs.get(&type_id) {
            for (_, handler) in handlers {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    handler(&event as &dyn Any);
                }));

                if let Err(e) = result {
                    eprintln!(
                        "Handler panicked for event {}: {:?}",
                        std::any::type_name::<E>(),
                        e
                    );
                }
            }
        }
    }

    /// Remove all subscriptions.
    pub fn clear(&self) {
        self.subscribers.write().unwrap().clear();
    }

    /// Count of event types with active subscriptions.
    pub fn event_type_count(&self) -> usize {
        self.subscribers.read().unwrap().len()
    }

    /// Count of handlers for a specific event type.
    pub fn handler_count<E>(&self) -> usize
    where
        E: Event + 'static,
    {
        let type_id = TypeId::of::<E>();
        self.subscribers
            .read()
            .unwrap()
            .get(&type_id)
            .map_or(0, |v| v.len())
    }
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    #[derive(Debug, Clone)]
    struct TestEvent {
        value: i32,
    }
    impl Event for TestEvent {}

    #[derive(Debug, Clone)]
    struct OtherEvent;
    impl Event for OtherEvent {}

    #[test]
    fn subscribe_and_publish() {
        let bus = EventBus::new();
        let counter = Arc::new(AtomicUsize::new(0));
        let counter_clone = Arc::clone(&counter);

        bus.subscribe(move |_: &TestEvent| {
            counter_clone.fetch_add(1, Ordering::SeqCst);
        });

        bus.publish(TestEvent { value: 42 });
        assert_eq!(counter.load(Ordering::SeqCst), 1);

        bus.publish(TestEvent { value: 99 });
        assert_eq!(counter.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn multiple_handlers() {
        let bus = EventBus::new();
        let sum = Arc::new(AtomicUsize::new(0));

        let sum1 = Arc::clone(&sum);
        bus.subscribe(move |e: &TestEvent| {
            sum1.fetch_add(e.value as usize, Ordering::SeqCst);
        });

        let sum2 = Arc::clone(&sum);
        bus.subscribe(move |e: &TestEvent| {
            sum2.fetch_add(e.value as usize * 2, Ordering::SeqCst);
        });

        bus.publish(TestEvent { value: 10 });
        assert_eq!(sum.load(Ordering::SeqCst), 10 + 20);
    }

    #[test]
    fn event_type_isolation() {
        let bus = EventBus::new();
        let test_counter = Arc::new(AtomicUsize::new(0));
        let other_counter = Arc::new(AtomicUsize::new(0));

        let tc = Arc::clone(&test_counter);
        bus.subscribe(move |_: &TestEvent| {
            tc.fetch_add(1, Ordering::SeqCst);
        });

        let oc = Arc::clone(&other_counter);
        bus.subscribe(move |_: &OtherEvent| {
            oc.fetch_add(1, Ordering::SeqCst);
        });

        bus.publish(TestEvent { value: 1 });
        assert_eq!(test_counter.load(Ordering::SeqCst), 1);
        assert_eq!(other_counter.load(Ordering::SeqCst), 0);

        bus.publish(OtherEvent);
        assert_eq!(test_counter.load(Ordering::SeqCst), 1);
        assert_eq!(other_counter.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn handler_panic_isolation() {
        let bus = EventBus::new();
        let safe_counter = Arc::new(AtomicUsize::new(0));

        bus.subscribe(move |_: &TestEvent| {
            panic!("Handler panic test");
        });

        let sc = Arc::clone(&safe_counter);
        bus.subscribe(move |_: &TestEvent| {
            sc.fetch_add(1, Ordering::SeqCst);
        });

        bus.publish(TestEvent { value: 1 });
        assert_eq!(safe_counter.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn clear() {
        let bus = EventBus::new();
        bus.subscribe(|_: &TestEvent| {});
        bus.subscribe(|_: &OtherEvent| {});

        assert_eq!(bus.event_type_count(), 2);
        bus.clear();
        assert_eq!(bus.event_type_count(), 0);
    }

    #[test]
    fn handler_count() {
        let bus = EventBus::new();
        assert_eq!(bus.handler_count::<TestEvent>(), 0);

        bus.subscribe(|_: &TestEvent| {});
        assert_eq!(bus.handler_count::<TestEvent>(), 1);

        bus.subscribe(|_: &TestEvent| {});
        assert_eq!(bus.handler_count::<TestEvent>(), 2);
    }

    // ── Python parity: test_event_bus.py ────────────────────────────

    #[test]
    fn unsubscribe_removes_handler() {
        // Mirrors `test_unsubscribe_removes_handler`.
        let bus = EventBus::new();
        let counter = Arc::new(AtomicUsize::new(0));
        let counter_clone = Arc::clone(&counter);

        let id = bus.subscribe(move |_: &TestEvent| {
            counter_clone.fetch_add(1, Ordering::SeqCst);
        });
        assert!(bus.unsubscribe::<TestEvent>(id));

        bus.publish(TestEvent { value: 1 });
        assert_eq!(counter.load(Ordering::SeqCst), 0);
        assert_eq!(bus.handler_count::<TestEvent>(), 0);
    }

    #[test]
    fn unsubscribe_unknown_id_returns_false() {
        // Mirrors `test_publish_raises_for_unknown_subscriber_on_unsubscribe`:
        // Python raises ValueError; Rust reports absence as `false`.
        let bus = EventBus::new();
        let id = bus.subscribe(|_: &TestEvent| {});
        assert!(!bus.unsubscribe::<TestEvent>(HandlerId(id.0 + 1)));
        assert!(!bus.unsubscribe::<OtherEvent>(id));
        assert_eq!(bus.handler_count::<TestEvent>(), 1);
    }

    #[test]
    fn unsubscribe_keeps_remaining_handlers_in_order() {
        // Only the targeted handler is removed; survivors keep
        // subscription order (Python `list.remove` semantics).
        let bus = EventBus::new();
        let order = Arc::new(std::sync::Mutex::new(Vec::new()));

        let o1 = Arc::clone(&order);
        let first = bus.subscribe(move |_: &TestEvent| {
            o1.lock().unwrap().push(1);
        });
        let o2 = Arc::clone(&order);
        let second = bus.subscribe(move |_: &TestEvent| {
            o2.lock().unwrap().push(2);
        });
        let o3 = Arc::clone(&order);
        bus.subscribe(move |_: &TestEvent| {
            o3.lock().unwrap().push(3);
        });

        assert!(bus.unsubscribe::<TestEvent>(second));
        // Double removal is a no-op `false`, like removing twice from a list.
        assert!(!bus.unsubscribe::<TestEvent>(second));

        bus.publish(TestEvent { value: 1 });
        assert_eq!(*order.lock().unwrap(), vec![1, 3]);
        let _ = first;
    }
}
