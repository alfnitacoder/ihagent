module.exports = {
  presets: [
    [
      '@babel/preset-env',
      {
        // Force CJS so webpack does not re-parse leftover import/export
        // (avoids: "may appear only with sourceType: module" / javascript/dynamic).
        modules: 'commonjs',
      },
    ],
    ['@babel/preset-react', { runtime: 'classic' }],
  ],
};
