import { defineConfig } from 'vitepress'

// The site is served from the project page on GitHub Pages:
// https://mlibre.github.io/Terminal-AI-Helper/  — hence the base. A custom
// domain served from the root would change these three things together:
// `base`, the sitemap hostname and the absolute URLs in the head below.
const SITE = 'https://mlibre.github.io'
const BASE = '/Terminal-AI-Helper/'
const GITHUB = 'https://github.com/mlibre/Terminal-AI-Helper'

const DESCRIPTIONS = {
  'index.md':
    'TAI — Terminal AI Helper. A shell plugin for bash and zsh that learns ' +
    'the commands you actually run and suggests them as you type: ghost ' +
    'hints, ranked menus, typo correction. Local, offline, no daemon.',
  'usage.md':
    'Install TAI in one command, learn the six keys, and meet the localhost ' +
    'dashboard: everything the plugin learned, with the why behind every ' +
    'suggestion.',
  'architecture.md':
    'How TAI is put together: a spool, a SQLite store, a build-time ranker ' +
    'and two shell-sourceable indexes — the keystroke path is an ' +
    'associative-array lookup, nothing more.',
  'development.md':
    'Working on TAI: the layout, the suites that hold the product promises, ' +
    'the pty harness, and what to verify before a change lands.'
}

export default defineConfig({
  lang: 'en',
  title: 'Terminal AI Helper',
  description: DESCRIPTIONS['index.md'],
  base: BASE,
  sitemap: {
    hostname: SITE,
    // vitepress builds the sitemap from page paths alone and resolves them
    // against the hostname — the base never lands in a <loc>. This hook is
    // the sanctioned place to put it back.
    transformItems: (items) =>
      items.map((it) => ({ ...it, url: BASE + it.url }))
  },
  ignoreDeadLinks: false,

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: BASE + 'favicon.svg' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Terminal AI Helper' }],
    ['meta', { property: 'og:title', content: 'Terminal AI Helper' }],
    ['meta', { property: 'og:description', content: DESCRIPTIONS['index.md'] }],
    ['meta', { property: 'og:url', content: SITE + BASE }],
    ['meta', { property: 'og:image', content: SITE + BASE + 'og.png' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:title', content: 'Terminal AI Helper' }],
    ['meta', { name: 'twitter:description', content: DESCRIPTIONS['index.md'] }],
    ['meta', { name: 'twitter:image', content: SITE + BASE + 'og.png' }],
    ['meta', { name: 'theme-color', content: '#0d1117' }]
  ],

  transformPageData(pageData) {
    const desc = DESCRIPTIONS[pageData.relativePath]
    if (desc) {
      // the page's real URL: index.md is the directory itself
      const html = (pageData.relativePath || '')
        .replace(/(^|\/)index\.md$/, '$1')
        .replace(/\.md$/, '.html')
      pageData.frontmatter.description = desc
      pageData.frontmatter.head ??= []
      pageData.frontmatter.head.push(
        ['meta', { name: 'description', content: desc }],
        ['meta', { property: 'og:description', content: desc }],
        ['meta', { property: 'og:title', content: pageData.title }],
        ['meta', { property: 'og:url', content: SITE + BASE + html }]
      )
    }
  },

  themeConfig: {
    siteTitle: 'Terminal AI Helper',
    nav: [
      { text: 'Usage', link: '/usage' },
      { text: 'Architecture', link: '/architecture' },
      { text: 'Development', link: '/development' },
      { text: 'GitHub', link: GITHUB }
    ],
    sidebar: [
      {
        text: 'Docs',
        items: [
          { text: 'Usage — install, the keys, the dashboard', link: '/usage' },
          { text: 'Architecture — why it is built this way', link: '/architecture' },
          { text: 'Development — tests and the layout', link: '/development' }
        ]
      }
    ],
    socialLinks: [{ icon: 'github', link: GITHUB }],
    search: { provider: 'local' },
    outline: { level: [2, 3], label: 'On this page' },
    docFooter: { prev: 'Previous', next: 'Next' },
    lastUpdated: { text: 'Last updated' },
    footer: {
      message:
        '<a href="https://www.unrwa.org">In support of Palestine 🇵🇸</a> — donate if you can.'
    }
  }
})
