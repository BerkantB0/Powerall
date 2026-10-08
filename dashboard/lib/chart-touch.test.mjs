import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attachChartTouchPreview } from './chart-touch.ts';

function fixture(t) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const handlers = new Map();
  const buttons = [0, 1, 2].map(index => ({
    dataset: { barIndex: String(index) },
    closest() { return this; },
    getBoundingClientRect() { return { left: index * 100, width: 80 }; },
  }));
  const element = {
    contains: button => buttons.includes(button),
    querySelectorAll: () => buttons,
    getBoundingClientRect: () => ({ top: 0, bottom: 200 }),
    addEventListener: (type, handler) => handlers.set(type, handler),
    removeEventListener: type => handlers.delete(type),
  };
  const previews = [];
  const cleanup = attachChartTouchPreview(element, i => previews.push(i));
  t.after(cleanup);
  const fire = (type, x = 40, y = 80, count = 1, detail = 1) => {
    const event = { type, target: buttons[0], cancelable: true, detail,
      touches: Array.from({ length: count }, () => ({ clientX: x, clientY: y })),
      prevented: false, stopped: false,
      preventDefault() { this.prevented = true; },
      stopImmediatePropagation() { this.stopped = true; },
    };
    handlers.get(type)?.(event);
    return event;
  };
  return { fire, previews, cleanup, handlers };
}

test('quick tap allows daily click and never activates hold', t => {
  const f = fixture(t); f.fire('touchstart'); t.mock.timers.tick(100);
  assert.equal(f.fire('touchend', 40, 80, 0).prevented, false);
  assert.equal(f.fire('click').stopped, false);
  t.mock.timers.tick(500); assert.deepEqual(f.previews, []);
});
test('hold previews, slide follows nearest bars including gaps, release suppresses drill-down', t => {
  const f = fixture(t); f.fire('touchstart'); t.mock.timers.tick(450);
  assert.deepEqual(f.previews, [0]);
  assert.equal(f.fire('touchmove', 190).prevented, true);
  assert.deepEqual(f.previews, [0, 1]);
  f.fire('touchmove', 240); assert.equal(f.previews.at(-1), 2);
  assert.equal(f.fire('touchend', 240, 80, 0).prevented, true);
  assert.equal(f.fire('click').stopped, true);
  // A fresh tap must not inherit suppression from the previous hold.
  f.fire('touchstart'); f.fire('touchend', 40, 80, 0);
  assert.equal(f.fire('click').stopped, false);
});
test('ordinary horizontal and vertical swipes cancel hold and allow scrolling', t => {
  const f = fixture(t);
  for (const [x, y] of [[70,80], [40,110]]) {
    f.fire('touchstart'); assert.equal(f.fire('touchmove',x,y).prevented, false);
    t.mock.timers.tick(600); assert.deepEqual(f.previews, []);
    f.fire('touchend', x, y, 0); assert.equal(f.fire('click').stopped, true);
  }
});
test('cancellation, multitouch and cleanup stop pending previews', t => {
  const f = fixture(t); f.fire('touchstart'); f.fire('touchcancel', 40, 80, 0);
  t.mock.timers.tick(500); assert.deepEqual(f.previews, []);
  f.fire('touchstart'); assert.equal(f.fire('touchmove',40,80,2).prevented, false);
  t.mock.timers.tick(500); assert.deepEqual(f.previews, []);
  f.fire('touchstart'); f.cleanup(); t.mock.timers.tick(500);
  assert.deepEqual(f.previews, []); assert.equal(f.handlers.size, 0);
});
test('keyboard activation remains available and fallback suppression expires', t => {
  const f = fixture(t); f.fire('touchstart'); t.mock.timers.tick(450);
  f.fire('touchend',40,80,0);
  assert.equal(f.fire('click',40,80,0,0).stopped, false);
  t.mock.timers.tick(1000); assert.equal(f.fire('click').stopped, false);
});
