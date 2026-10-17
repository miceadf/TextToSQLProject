import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { it } from 'node:test';
import ts from 'typescript';
import { disabledTitle } from '../../src/adaptive/adminStyles.ts';
import { focusReviewer, readReviewer, REVIEWER_STORAGE_KEY, writeReviewer } from '../../src/adaptive/reviewerMemory.ts';

function withStorage(descriptor: PropertyDescriptor, run: () => void) {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, ...descriptor });
  try {
    run();
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
}

it('remembers edits, clearing and reopening across DBs with one application key', () => {
  const values = new Map<string, string>();
  withStorage({ value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  } }, () => {
    assert.equal(readReviewer(), '');
    writeReviewer('검토자 이름');
    assert.equal(readReviewer(), '검토자 이름');
    assert.deepEqual([...values.keys()], [REVIEWER_STORAGE_KEY]);
    writeReviewer('');
    assert.equal(readReviewer(), '');
  });
});

it('continues without storage when access, reads or writes are blocked', () => {
  const blocked = () => { throw new Error('blocked'); };
  for (const descriptor of [{ get: blocked }, { value: { getItem: blocked, setItem: blocked } }]) {
    withStorage(descriptor, () => {
      assert.equal(readReviewer(), '');
      assert.doesNotThrow(() => writeReviewer('in-memory reviewer'));
    });
  }
  withStorage({ value: undefined }, () => {
    assert.equal(readReviewer(), '');
    assert.doesNotThrow(() => writeReviewer('name'));
  });
});

it('scrolls to and focuses the reviewer without a second focus scroll', () => {
  const calls: unknown[] = [];
  const input = {
    scrollIntoView: (options: unknown) => calls.push(['scroll', options]),
    focus: (options: unknown) => calls.push(['focus', options]),
  } as unknown as HTMLInputElement;
  focusReviewer(input);
  assert.deepEqual(calls, [
    ['scroll', { block: 'center', behavior: 'smooth' }],
    ['focus', { preventScroll: true }],
  ]);
  assert.doesNotThrow(() => focusReviewer(null));
});

it('shows only a current blocking reason and follows condition priority', () => {
  assert.equal(disabledTitle([false, 'busy'], [false, 'reviewer']), undefined);
  assert.equal(disabledTitle([false, 'busy'], [true, 'reviewer']), 'reviewer');
  assert.equal(disabledTitle([true, 'busy'], [true, 'reviewer']), 'busy');
});

it('wires shared reviewer hints and scoped targets for all review tools', () => {
  for (const name of ['MappingReviewPanel', 'ValueDictionaryPanel', 'ExtensionFieldPanel', 'TestQueryPanel']) {
    const source = readFileSync(`src/adaptive/${name}.tsx`, 'utf8');
    assert.match(source, /reviewer\.trim\(\) === '' \? <ReviewerHint \/>/);
    assert.doesNotMatch(source, /먼저 검토자 이름을 넣어 주세요/);
    if (name !== 'TestQueryPanel') {
      assert.match(source, /useState\(readReviewer\)/);
      assert.match(source, /writeReviewer\(/);
      assert.match(source, /data-reviewer-scope/);
      assert.match(source, /data-reviewer-input value=\{reviewer\}/);
    }
  }
  const hint = readFileSync('src/adaptive/ReviewerHint.tsx', 'utf8');
  assert.match(hint, /type="button"/);
  assert.match(hint, /closest\('\[data-reviewer-scope\]'\)/);
  assert.match(hint, /맨 위 ‘검토자’ 칸에 이름을 넣어 주세요/);
});

it('uses shared styles and explains every explicitly disabled admin button', () => {
  const files = ['PostgresRegistrationPanel', 'MappingReviewPanel', 'MappingReviewRow', 'ValueDictionaryPanel',
    'CodeTableImport', 'ExtensionFieldPanel', 'TestQueryPanel', 'SchemaComparePanel'];
  for (const name of files) {
    const source = readFileSync(`src/adaptive/${name}.tsx`, 'utf8');
    const ast = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function visit(node: ts.Node) {
      if (ts.isJsxOpeningElement(node) && node.tagName.getText(ast) === 'button') {
        const attrs = node.attributes.properties;
        const has = (key: string) => attrs.some(a => ts.isJsxAttribute(a) && a.name.getText(ast) === key);
        assert.ok(has('className'), `${name}: button style`);
        if (has('disabled')) assert.ok(has('title'), `${name}: disabled reason`);
      }
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
});
