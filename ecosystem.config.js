// ⚠️ 此文件已废弃 ⚠️
// 请使用 ecosystem.config.cjs
// 
// 由于 package.json 中定义了 "type": "module"，.js 文件会被视为 ES Module。
// 而 PM2 配置文件通常使用 CommonJS (require/module.exports)。
// 因此我们将配置文件重命名为 ecosystem.config.cjs 以强制使用 CJS 模式。

console.log("Please use ecosystem.config.cjs");
export default {};
