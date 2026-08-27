// הקונפיג נדרש רק בשביל launcher אחד: ChromeHeadlessNoSandbox.
// בתוך קונטיינר (CI, Docker) אין namespace ל-sandbox של כרום, ובלי
// הדגל הבדיקות פשוט לא עולות. מחוץ לקונטיינר זה עדיין עובד.
module.exports = function (config) {
  config.set({
    basePath: '',
    frameworks: ['jasmine', '@angular-devkit/build-angular'],
    plugins: [
      require('karma-jasmine'),
      require('karma-chrome-launcher'),
      require('karma-jasmine-html-reporter'),
      require('karma-coverage'),
      require('@angular-devkit/build-angular/plugins/karma'),
    ],
    client: { jasmine: {}, clearContext: false },
    jasmineHtmlReporter: { suppressAll: true },
    coverageReporter: {
      dir: require('path').join(__dirname, './coverage'),
      subdir: '.',
      reporters: [{ type: 'text-summary' }],
    },
    reporters: ['progress', 'kjhtml'],
    browsers: ['Chrome'],
    customLaunchers: {
      ChromeHeadlessNoSandbox: {
        base: 'ChromeHeadless',
        flags: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
      },
    },
    restartOnFileChange: true,
  });
};
