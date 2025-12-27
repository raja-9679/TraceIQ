const xpath = require('xpath');
const { DOMParser } = require('@xmldom/xmldom');

const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url>
    <loc>https://www.thehindu.com/news/national/top-indian-national-congress-leaders-working-committee-meeting-new-delhi-mgnrega/article70442726.ece</loc>
    <lastmod>2025-12-27T16:08:18+05:30</lastmod>
    <news:news>
      <news:publication>
        <news:name>The Hindu</news:name>
        <news:language>en</news:language>
      </news:publication>
      <news:publication_date>2025-12-27T12:19:33+05:30</news:publication_date>
      <news:title>Congress plans ‘MGNREGA Bachao Abhiyan’ from Jan. 5; Kharge says Modi govt. will face people’s anger</news:title>
      <news:keywords>Breaking news, Indian National Congress</news:keywords>
    </news:news>
    <image:image>
      <image:loc>https://th-i.thgim.com/public/incoming/kjfcqc/article70442741.ece/alternates/FREE_1200/WhatsApp%20Image%202025-12-27%20at%2012.01.01%20PM.jpeg</image:loc>
    </image:image>
  </url>
</urlset>`;

const doc = new DOMParser().parseFromString(xml, 'text/xml');

const paths = [
  "/*[local-name()='urlset' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9']",
  "/*[local-name()='urlset' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9']/*[local-name()='url' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9'][1]",
  "/*[local-name()='urlset' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9']/*[local-name()='url' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9'][1]/*[local-name()='loc' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9'][1]",
  "/*[local-name()='urlset' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9']/*[local-name()='url' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9'][1]/*[local-name()='news' and namespace-uri()='http://www.google.com/schemas/sitemap-news/0.9'][1]",
  "/*[local-name()='urlset' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9']/*[local-name()='url' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9'][1]/*[local-name()='news' and namespace-uri()='http://www.google.com/schemas/sitemap-news/0.9'][1]/*[local-name()='publication' and namespace-uri()='http://www.google.com/schemas/sitemap-news/0.9'][1]",
  "/*[local-name()='urlset' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9']/*[local-name()='url' and namespace-uri()='https://www.sitemaps.org/schemas/sitemap/0.9'][1]/*[local-name()='news' and namespace-uri()='http://www.google.com/schemas/sitemap-news/0.9'][1]/*[local-name()='publication' and namespace-uri()='http://www.google.com/schemas/sitemap-news/0.9'][1]/*[local-name()='name' and namespace-uri()='http://www.google.com/schemas/sitemap-news/0.9'][1]"
];

console.log("--- Testing Namespace XML ---");
paths.forEach(path => {
  try {
    const nodes = xpath.select(path, doc);
    console.log(`Path: ${path} -> Found: ${nodes.length > 0}`);
  } catch (e) {
    console.log(`Path: ${path} -> Error: ${e.message}`);
  }
});

const xmlNoNs = `<root><child>text</child></root>`;
const docNoNs = new DOMParser().parseFromString(xmlNoNs, 'text/xml');
console.log("\n--- Testing No Namespace XML ---");
const pathsNoNs = ["/root", "/*[name()='root']", "/*[name()='root']/*[name()='child']"];
pathsNoNs.forEach(path => {
  const nodes = xpath.select(path, docNoNs);
  console.log(`Path: ${path} -> Found: ${nodes.length > 0}`);
});
