/******/ (() => { // webpackBootstrap
/******/ 	"use strict";
/******/ 	var __webpack_modules__ = ({

/***/ 42613:
/***/ ((module) => {

module.exports = require("assert");

/***/ }),

/***/ 20181:
/***/ ((module) => {

module.exports = require("buffer");

/***/ }),

/***/ 35317:
/***/ ((module) => {

module.exports = require("child_process");

/***/ }),

/***/ 49140:
/***/ ((module) => {

module.exports = require("constants");

/***/ }),

/***/ 76982:
/***/ ((module) => {

module.exports = require("crypto");

/***/ }),

/***/ 24434:
/***/ ((module) => {

module.exports = require("events");

/***/ }),

/***/ 79896:
/***/ ((module) => {

module.exports = require("fs");

/***/ }),

/***/ 91943:
/***/ ((module) => {

module.exports = require("fs/promises");

/***/ }),

/***/ 58611:
/***/ ((module) => {

module.exports = require("http");

/***/ }),

/***/ 65692:
/***/ ((module) => {

module.exports = require("https");

/***/ }),

/***/ 69278:
/***/ ((module) => {

module.exports = require("net");

/***/ }),

/***/ 34589:
/***/ ((module) => {

module.exports = require("node:assert");

/***/ }),

/***/ 16698:
/***/ ((module) => {

module.exports = require("node:async_hooks");

/***/ }),

/***/ 4573:
/***/ ((module) => {

module.exports = require("node:buffer");

/***/ }),

/***/ 37540:
/***/ ((module) => {

module.exports = require("node:console");

/***/ }),

/***/ 77598:
/***/ ((module) => {

module.exports = require("node:crypto");

/***/ }),

/***/ 53053:
/***/ ((module) => {

module.exports = require("node:diagnostics_channel");

/***/ }),

/***/ 40610:
/***/ ((module) => {

module.exports = require("node:dns");

/***/ }),

/***/ 78474:
/***/ ((module) => {

module.exports = require("node:events");

/***/ }),

/***/ 73024:
/***/ ((module) => {

module.exports = require("node:fs");

/***/ }),

/***/ 51455:
/***/ ((module) => {

module.exports = require("node:fs/promises");

/***/ }),

/***/ 37067:
/***/ ((module) => {

module.exports = require("node:http");

/***/ }),

/***/ 32467:
/***/ ((module) => {

module.exports = require("node:http2");

/***/ }),

/***/ 44708:
/***/ ((module) => {

module.exports = require("node:https");

/***/ }),

/***/ 77030:
/***/ ((module) => {

module.exports = require("node:net");

/***/ }),

/***/ 48161:
/***/ ((module) => {

module.exports = require("node:os");

/***/ }),

/***/ 76760:
/***/ ((module) => {

module.exports = require("node:path");

/***/ }),

/***/ 643:
/***/ ((module) => {

module.exports = require("node:perf_hooks");

/***/ }),

/***/ 1708:
/***/ ((module) => {

module.exports = require("node:process");

/***/ }),

/***/ 41792:
/***/ ((module) => {

module.exports = require("node:querystring");

/***/ }),

/***/ 57075:
/***/ ((module) => {

module.exports = require("node:stream");

/***/ }),

/***/ 46193:
/***/ ((module) => {

module.exports = require("node:string_decoder");

/***/ }),

/***/ 41692:
/***/ ((module) => {

module.exports = require("node:tls");

/***/ }),

/***/ 73136:
/***/ ((module) => {

module.exports = require("node:url");

/***/ }),

/***/ 57975:
/***/ ((module) => {

module.exports = require("node:util");

/***/ }),

/***/ 73429:
/***/ ((module) => {

module.exports = require("node:util/types");

/***/ }),

/***/ 75919:
/***/ ((module) => {

module.exports = require("node:worker_threads");

/***/ }),

/***/ 38522:
/***/ ((module) => {

module.exports = require("node:zlib");

/***/ }),

/***/ 70857:
/***/ ((module) => {

module.exports = require("os");

/***/ }),

/***/ 16928:
/***/ ((module) => {

module.exports = require("path");

/***/ }),

/***/ 2203:
/***/ ((module) => {

module.exports = require("stream");

/***/ }),

/***/ 13193:
/***/ ((module) => {

module.exports = require("string_decoder");

/***/ }),

/***/ 53557:
/***/ ((module) => {

module.exports = require("timers");

/***/ }),

/***/ 64756:
/***/ ((module) => {

module.exports = require("tls");

/***/ }),

/***/ 52018:
/***/ ((module) => {

module.exports = require("tty");

/***/ }),

/***/ 87016:
/***/ ((module) => {

module.exports = require("url");

/***/ }),

/***/ 39023:
/***/ ((module) => {

module.exports = require("util");

/***/ }),

/***/ 43106:
/***/ ((module) => {

module.exports = require("zlib");

/***/ }),

/***/ 11347:
/***/ ((module, __unused_webpack_exports, __nccwpck_require__) => {

/* module decorator */ module = __nccwpck_require__.nmd(module);

const fs = __nccwpck_require__(73024);
const path = __nccwpck_require__(76760);

function plan(raw, workspace) {
  const indices = JSON.parse(raw);
  if (!Array.isArray(indices) || indices.length < 1 || indices.length > 4 ||
      indices.some((n, i) => !Number.isInteger(n) || n !== i)) {
    throw new Error('Invalid source chunk matrix');
  }
  const root = path.resolve(workspace, 'parallel-media');
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error('Invalid media directory');
  // Validate every chunk before starting any transfer. Never upload directories,
  // symlinks, secrets, source URLs or arbitrary user-provided paths.
  return indices.map(index => {
    const file = path.join(root, `part-${String(index).padStart(2, '0')}.mkv`);
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1) throw new Error('Missing source chunk');
    return {index, file, root, name: `parallel-source-${index}`};
  });
}

async function uploadAll(client, jobs, log = console.log) {
  const started = Date.now();
  log(`Uploading ${jobs.length} source chunk artifacts concurrently; compression disabled`);
  const results = await Promise.allSettled(jobs.map(async job => {
    const begin = Date.now();
    log(`Source chunk ${job.index + 1}: artifact upload starting`);
    const result = await client.uploadArtifact(job.name, [job.file], job.root,
      {compressionLevel: 0, retentionDays: 1});
    if (!Number.isSafeInteger(result.id) || result.id <= 0) throw new Error('Artifact acceptance not confirmed');
    log(`Source chunk ${job.index + 1}: artifact upload complete | elapsed ${((Date.now() - begin) / 1000).toFixed(1)}s`);
  }));
  // Let all in-flight requests settle. A partial upload must never start workers.
  if (results.some(result => result.status === 'rejected')) {
    throw new Error('One or more source artifacts could not be confirmed. Encoding workers were not started.');
  }
  log(`All source artifacts confirmed | elapsed ${((Date.now() - started) / 1000).toFixed(1)}s`);
}

async function createClient() {
  const {DefaultArtifactClient} = await __nccwpck_require__.e(/* import() */ 354).then(__nccwpck_require__.bind(__nccwpck_require__, 42354));
  return new DefaultArtifactClient();
}

async function main() {
  const jobs = plan(process.env.INPUT_MATRIX || '', process.env.GITHUB_WORKSPACE || process.cwd());
  await uploadAll(await createClient(), jobs);
}

module.exports = {plan, uploadAll, createClient};
if (__nccwpck_require__.c[__nccwpck_require__.s] === module) main().catch(() => {
  // Do not emit raw SDK exception bodies, signed endpoints or runtime tokens.
  console.error('::error::Source artifact upload failed. Workers were not started; check artifact storage quota and GitHub service availability.');
  process.exitCode = 1;
});


/***/ })

/******/ 	});
/************************************************************************/
/******/ 	// The module cache
/******/ 	var __webpack_module_cache__ = {};
/******/ 	
/******/ 	// The require function
/******/ 	function __nccwpck_require__(moduleId) {
/******/ 		// Check if module is in cache
/******/ 		var cachedModule = __webpack_module_cache__[moduleId];
/******/ 		if (cachedModule !== undefined) {
/******/ 			return cachedModule.exports;
/******/ 		}
/******/ 		// Create a new module (and put it into the cache)
/******/ 		var module = __webpack_module_cache__[moduleId] = {
/******/ 			id: moduleId,
/******/ 			loaded: false,
/******/ 			exports: {}
/******/ 		};
/******/ 	
/******/ 		// Execute the module function
/******/ 		var threw = true;
/******/ 		try {
/******/ 			__webpack_modules__[moduleId].call(module.exports, module, module.exports, __nccwpck_require__);
/******/ 			threw = false;
/******/ 		} finally {
/******/ 			if(threw) delete __webpack_module_cache__[moduleId];
/******/ 		}
/******/ 	
/******/ 		// Flag the module as loaded
/******/ 		module.loaded = true;
/******/ 	
/******/ 		// Return the exports of the module
/******/ 		return module.exports;
/******/ 	}
/******/ 	
/******/ 	// expose the modules object (__webpack_modules__)
/******/ 	__nccwpck_require__.m = __webpack_modules__;
/******/ 	
/******/ 	// expose the module cache
/******/ 	__nccwpck_require__.c = __webpack_module_cache__;
/******/ 	
/************************************************************************/
/******/ 	/* webpack/runtime/create fake namespace object */
/******/ 	(() => {
/******/ 		var getProto = Object.getPrototypeOf ? (obj) => (Object.getPrototypeOf(obj)) : (obj) => (obj.__proto__);
/******/ 		var leafPrototypes;
/******/ 		// create a fake namespace object
/******/ 		// mode & 1: value is a module id, require it
/******/ 		// mode & 2: merge all properties of value into the ns
/******/ 		// mode & 4: return value when already ns object
/******/ 		// mode & 16: return value when it's Promise-like
/******/ 		// mode & 8|1: behave like require
/******/ 		__nccwpck_require__.t = function(value, mode) {
/******/ 			if(mode & 1) value = this(value);
/******/ 			if(mode & 8) return value;
/******/ 			if(typeof value === 'object' && value) {
/******/ 				if((mode & 4) && value.__esModule) return value;
/******/ 				if((mode & 16) && typeof value.then === 'function') return value;
/******/ 			}
/******/ 			var ns = Object.create(null);
/******/ 			__nccwpck_require__.r(ns);
/******/ 			var def = {};
/******/ 			leafPrototypes = leafPrototypes || [null, getProto({}), getProto([]), getProto(getProto)];
/******/ 			for(var current = mode & 2 && value; typeof current == 'object' && !~leafPrototypes.indexOf(current); current = getProto(current)) {
/******/ 				Object.getOwnPropertyNames(current).forEach((key) => (def[key] = () => (value[key])));
/******/ 			}
/******/ 			def['default'] = () => (value);
/******/ 			__nccwpck_require__.d(ns, def);
/******/ 			return ns;
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/define property getters */
/******/ 	(() => {
/******/ 		// define getter functions for harmony exports
/******/ 		__nccwpck_require__.d = (exports, definition) => {
/******/ 			for(var key in definition) {
/******/ 				if(__nccwpck_require__.o(definition, key) && !__nccwpck_require__.o(exports, key)) {
/******/ 					Object.defineProperty(exports, key, { enumerable: true, get: definition[key] });
/******/ 				}
/******/ 			}
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/ensure chunk */
/******/ 	(() => {
/******/ 		__nccwpck_require__.f = {};
/******/ 		// This file contains only the entry chunk.
/******/ 		// The chunk loading function for additional chunks
/******/ 		__nccwpck_require__.e = (chunkId) => {
/******/ 			return Promise.all(Object.keys(__nccwpck_require__.f).reduce((promises, key) => {
/******/ 				__nccwpck_require__.f[key](chunkId, promises);
/******/ 				return promises;
/******/ 			}, []));
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/get javascript chunk filename */
/******/ 	(() => {
/******/ 		// This function allow to reference async chunks
/******/ 		__nccwpck_require__.u = (chunkId) => {
/******/ 			// return url for filenames based on template
/******/ 			return "" + chunkId + ".index.cjs.js";
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/hasOwnProperty shorthand */
/******/ 	(() => {
/******/ 		__nccwpck_require__.o = (obj, prop) => (Object.prototype.hasOwnProperty.call(obj, prop))
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/make namespace object */
/******/ 	(() => {
/******/ 		// define __esModule on exports
/******/ 		__nccwpck_require__.r = (exports) => {
/******/ 			if(typeof Symbol !== 'undefined' && Symbol.toStringTag) {
/******/ 				Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
/******/ 			}
/******/ 			Object.defineProperty(exports, '__esModule', { value: true });
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/node module decorator */
/******/ 	(() => {
/******/ 		__nccwpck_require__.nmd = (module) => {
/******/ 			module.paths = [];
/******/ 			if (!module.children) module.children = [];
/******/ 			return module;
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/compat */
/******/ 	
/******/ 	if (typeof __nccwpck_require__ !== 'undefined') __nccwpck_require__.ab = __dirname + "/";
/******/ 	
/******/ 	/* webpack/runtime/require chunk loading */
/******/ 	(() => {
/******/ 		// no baseURI
/******/ 		
/******/ 		// object to store loaded chunks
/******/ 		// "1" means "loaded", otherwise not loaded yet
/******/ 		var installedChunks = {
/******/ 			792: 1
/******/ 		};
/******/ 		
/******/ 		// no on chunks loaded
/******/ 		
/******/ 		var installChunk = (chunk) => {
/******/ 			var moreModules = chunk.modules, chunkIds = chunk.ids, runtime = chunk.runtime;
/******/ 			for(var moduleId in moreModules) {
/******/ 				if(__nccwpck_require__.o(moreModules, moduleId)) {
/******/ 					__nccwpck_require__.m[moduleId] = moreModules[moduleId];
/******/ 				}
/******/ 			}
/******/ 			if(runtime) runtime(__nccwpck_require__);
/******/ 			for(var i = 0; i < chunkIds.length; i++)
/******/ 				installedChunks[chunkIds[i]] = 1;
/******/ 		
/******/ 		};
/******/ 		
/******/ 		// require() chunk loading for javascript
/******/ 		__nccwpck_require__.f.require = (chunkId, promises) => {
/******/ 			// "1" is the signal for "already loaded"
/******/ 			if(!installedChunks[chunkId]) {
/******/ 				if(true) { // all chunks have JS
/******/ 					installChunk(require("./" + __nccwpck_require__.u(chunkId)));
/******/ 				} else installedChunks[chunkId] = 1;
/******/ 			}
/******/ 		};
/******/ 		
/******/ 		// no external install chunk
/******/ 		
/******/ 		// no HMR
/******/ 		
/******/ 		// no HMR manifest
/******/ 	})();
/******/ 	
/************************************************************************/
/******/ 	
/******/ 	// module cache are used so entry inlining is disabled
/******/ 	// startup
/******/ 	// Load entry module and return exports
/******/ 	var __webpack_exports__ = __nccwpck_require__(__nccwpck_require__.s = 11347);
/******/ 	module.exports = __webpack_exports__;
/******/ 	
/******/ })()
;