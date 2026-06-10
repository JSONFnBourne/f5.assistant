const fs = require('fs');
const path = require('path');
const MiniSearch = require('minisearch');

const DIRECTORIES_TO_INDEX = [
    'tmos',
    'f5os',
    'irules',
    'ltm',
    'dns',
    'apm',
    'asm',
    'sslo',
    'swg'
];

// Knowledge docs live one level above the webapp at F5/knowledge/
const ROOT_DIR = path.join(__dirname, '..', '..', 'knowledge');
const OUTPUT_FILE = path.join(__dirname, '..', 'public', 'search-index.json');

// Supported extensions
const EXTENSIONS = ['.md', '.tcl', '.txt'];

function walkDir(dir, fileList = []) {
    if (!fs.existsSync(dir)) return fileList;

    const files = fs.readdirSync(dir);

    for (const file of files) {
        const filePath = path.join(dir, file);
        if (fs.statSync(filePath).isDirectory()) {
            walkDir(filePath, fileList);
        } else {
            const ext = path.extname(filePath).toLowerCase();
            if (EXTENSIONS.includes(ext)) {
                fileList.push(filePath);
            }
        }
    }

    return fileList;
}

function chunkContent(content, filePath) {
    // Basic chunking: split by markdown headers or large chunks
    const chunks = [];
    const maxChunkLen = 1500; // character limit roughly
    let currChunk = "";

    const lines = content.split('\n');
    let heading = "General";

    for (const line of lines) {
        if (line.startsWith('#')) {
            // New heading
            if (currChunk.trim().length > 0) {
                chunks.push({
                    text: currChunk.trim(),
                    heading: heading
                });
                currChunk = "";
            }
            heading = line.replace(/^#+\s/, '').trim();
        }
        currChunk += line + '\n';

        if (currChunk.length > maxChunkLen) {
            chunks.push({
                text: currChunk.trim(),
                heading: heading
            });
            currChunk = "";
        }
    }

    if (currChunk.trim().length > 0) {
        chunks.push({
            text: currChunk.trim(),
            heading: heading
        });
    }

    return chunks;
}

function buildIndex() {
    console.log('Building search index...');
    const allFiles = [];

    for (const dirName of DIRECTORIES_TO_INDEX) {
        const fullDir = path.join(ROOT_DIR, dirName);
        walkDir(fullDir, allFiles);
    }

    if (allFiles.length === 0) {
        console.error(
            `build-search-index: no source files (${EXTENSIONS.join(', ')}) found under ${ROOT_DIR} ` +
            `in any of: ${DIRECTORIES_TO_INDEX.join(', ')}.\n` +
            'Refusing to write an empty search index — check that the knowledge/ tree exists next to webapp/.'
        );
        process.exit(1);
    }

    const documents = [];
    let docId = 1;

    for (const file of allFiles) {
        const relativePath = path.relative(ROOT_DIR, file);
        const content = fs.readFileSync(file, 'utf-8');
        const chunks = chunkContent(content, relativePath);

        for (const chunk of chunks) {
            documents.push({
                id: docId++,
                path: relativePath,
                heading: chunk.heading,
                text: chunk.text
            });
        }
    }

    const miniSearch = new MiniSearch({
        fields: ['text', 'heading', 'path'],
        storeFields: ['path', 'heading', 'text']
    });

    miniSearch.addAll(documents);

    const indexJson = miniSearch.toJSON();
    fs.writeFileSync(OUTPUT_FILE, JSON.stringify(indexJson));
    console.log(`Generated search-index.json with ${documents.length} chunks.`);
}

buildIndex();
