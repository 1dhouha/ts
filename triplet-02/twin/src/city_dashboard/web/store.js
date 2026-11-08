const OPERATOR = 'twin-dashboard-operator-0.1-local';
const REPORT = 'twin-dashboard-report-0.1-local';

export class FrameStore {
  constructor(clock = () => performance.now()) {
    this.clock = clock;
    this.generation = 0;
    this.clear();
  }
  clear() {
    this.generation += 1;
    this.frame = null;
    this.history = [];
    this.selection = null;
    this.receivedAt = -Infinity;
    this.failed = true;
  }
  begin() { return ++this.generation; }
  get stale() { return this.failed || this.clock() - this.receivedAt > 1500; }
  fail() { this.failed = true; }
  accept(frame, generation) {
    if (generation !== this.generation || !frame ||
        ![OPERATOR, REPORT].includes(frame.schema_version) ||
        typeof frame.run_id !== 'string' || !Number.isSafeInteger(frame.frame_seq) || frame.frame_seq < 0) return false;
    const operator = frame.schema_version === OPERATOR;
    if (frame.visibility !== (operator ? 'PRIVATE_OPERATOR_ONLY' : 'REPORTED_ONLY') ||
        !Array.isArray(operator ? frame.events : frame.records)) return false;
    const previous = this.frame;
    const sameSchema = previous?.schema_version === frame.schema_version;
    if (sameSchema && (operator || previous.run_id === frame.run_id) && frame.frame_seq < previous.frame_seq) return false;
    if (!sameSchema || previous.run_id !== frame.run_id) {
      this.selection = null;
      this.history = [];
    }
    this.frame = frame;
    this.history = (operator ? frame.events : frame.records).slice(-512);
    this.failed = false;
    this.receivedAt = this.clock();
    return true;
  }
}
