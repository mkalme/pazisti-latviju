/* Synthesized sound effects via WebAudio; no audio files needed. */
(function () {
  "use strict";
  window.App = window.App || {};

  var ctx = null;
  var sound = { muted: false };

  function ac() {
    if (!ctx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function tone(t0, freq, dur, type, peak) {
    var o = ctx.createOscillator();
    var g = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t0);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g);
    g.connect(ctx.destination);
    o.start(t0);
    o.stop(t0 + dur + 0.05);
  }

  var effects = {
    correct: function (t) {
      tone(t, 659, 0.1, "sine", 0.16);
      tone(t + 0.08, 988, 0.16, "sine", 0.16);
    },
    wrong: function (t) {
      tone(t, 196, 0.16, "square", 0.05);
      tone(t, 185, 0.16, "sawtooth", 0.05);
    },
    reveal: function (t) {
      tone(t, 415, 0.14, "sine", 0.14);
      tone(t + 0.13, 311, 0.24, "sine", 0.14);
    },
    finish: function (t) {
      tone(t, 523, 0.12, "sine", 0.15);
      tone(t + 0.11, 659, 0.12, "sine", 0.15);
      tone(t + 0.22, 784, 0.12, "sine", 0.15);
      tone(t + 0.33, 1047, 0.3, "sine", 0.15);
    },
    tick: function (t) {
      tone(t, 880, 0.05, "sine", 0.08);
    }
  };

  sound.play = function (name) {
    if (sound.muted || !effects[name]) return;
    try {
      if (!ac()) return;
      effects[name](ctx.currentTime);
    } catch (e) { /* audio is never worth crashing over */ }
  };

  sound.setMuted = function (m) { sound.muted = m; };

  App.sound = sound;
})();
