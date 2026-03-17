#!/usr/bin/env node
/**
 * Simple HTTPS static file server for local development.
 *
 * WebRTC requires a secure context (HTTPS or localhost). This server serves
 * the WHEP receiver files over HTTPS with a self-signed certificate,
 * or over HTTP on localhost (which browsers treat as secure).
 *
 * Usage:
 *   node server.js                  # HTTP on localhost:8080
 *   node server.js --port 3000      # HTTP on localhost:3000
 */

const http = require("http");
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
let port = 8080;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--port" && args[i + 1]) {
    port = parseInt(args[i + 1], 10);
    i++;
  }
}

const MIME_TYPES = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

const rootDir = __dirname;

const server = http.createServer((req, res) => {
  let filePath = path.join(rootDir, req.url === "/" ? "index.html" : req.url);

  // Security: prevent directory traversal
  if (!filePath.startsWith(rootDir)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  const ext = path.extname(filePath);
  const contentType = MIME_TYPES[ext] || "application/octet-stream";

  fs.readFile(filePath, (err, data) => {
    if (err) {
      if (err.code === "ENOENT") {
        res.writeHead(404);
        res.end("Not found");
      } else {
        res.writeHead(500);
        res.end("Internal server error");
      }
      return;
    }

    res.writeHead(200, { "Content-Type": contentType });
    res.end(data);
  });
});

server.listen(port, "0.0.0.0", () => {
  console.log(`Static file server running at:`);
  console.log(`  http://localhost:${port}/`);
  console.log(`\nServing files from: ${rootDir}`);
  console.log(
    `\nNote: localhost is treated as a secure context by browsers,`
  );
  console.log(`so WebRTC will work without HTTPS.`);
});
