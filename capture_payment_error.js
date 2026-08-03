/*
 * capture_payment_error.js
 *
 * Paste into the Chrome DevTools console on the recreation.gov checkout page
 * BEFORE clicking submit. It wraps fetch and XMLHttpRequest so that any failing
 * request (>=400) prints its full response body, which the Network summary view
 * doesn't show you.
 *
 * The payment POST returns a ~0.4 kB body on 500. That body is the diagnostic:
 * it normally carries a reason code distinguishing "inventory already gone"
 * from "card/tokenization rejected" from "cart session expired".
 *
 * Card data is redacted before anything is logged -- see redact(). Nothing is
 * sent anywhere; this only prints to your own console.
 *
 * After the failure, right-click the console output -> "Save as..." to keep it.
 */

(() => {
  const SENSITIVE = /(card[_-]?number|cardnumber|pan|cvv|cvc|security[_-]?code|account[_-]?number|expir|routing)/i;

  // Mask anything that looks like a card number, plus any value whose key
  // names a sensitive field. Applied to request bodies before logging.
  function redact(text) {
    if (typeof text !== "string") return text;
    let out = text.replace(/\b\d{12,19}\b/g, (m) => m.slice(0, 2) + "*".repeat(m.length - 6) + m.slice(-4));
    try {
      const obj = JSON.parse(out);
      const walk = (n) => {
        if (Array.isArray(n)) return n.map(walk);
        if (n && typeof n === "object") {
          const copy = {};
          for (const [k, v] of Object.entries(n)) {
            copy[k] = SENSITIVE.test(k) ? "[REDACTED]" : walk(v);
          }
          return copy;
        }
        return n;
      };
      out = JSON.stringify(walk(obj), null, 2);
    } catch (_) {
      /* not JSON -- the regex pass above still applied */
    }
    return out;
  }

  function report(kind, method, url, status, reqBody, respBody) {
    console.group(`%c[capture] ${kind} ${method} ${status} ${url}`, "color:#c00;font-weight:bold");
    console.log("request body:\n", redact(reqBody) || "(none)");
    console.log("response body:\n", respBody || "(empty)");
    console.groupEnd();
  }

  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const req = args[0];
    const url = typeof req === "string" ? req : req?.url;
    const method = (args[1]?.method || req?.method || "GET").toUpperCase();
    const reqBody = args[1]?.body;
    const resp = await origFetch.apply(this, args);
    if (resp.status >= 400) {
      // Clone so the page's own handler still gets an unread body.
      resp.clone().text().then((t) => report("fetch", method, url, resp.status, reqBody, t)).catch(() => {});
    }
    return resp;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__cap = { method, url };
    return origOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (body) {
    this.addEventListener("load", () => {
      if (this.status >= 400) {
        const { method, url } = this.__cap || {};
        report("xhr", method, url, this.status, body, this.responseText);
      }
    });
    return origSend.call(this, body);
  };

  console.log("%c[capture] armed -- failing requests will print here", "color:#080;font-weight:bold");
})();
