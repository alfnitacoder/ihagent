module.exports = {
  presets: [
    [
      '@babel/preset-env',
      {
        // Force CJS so webpack does not re-parse leftover import/export
        // (avoids: "may appear only with sourceType: module" / javascript/dynamic).
        // Keep import() so the 3D robot stays in its own chunk.
        modules: 'commonjs',
        exclude: ['transform-dynamic-import'],
      },
    ],
    ['@babel/preset-react', { runtime: 'classic' }],
  ],
};
