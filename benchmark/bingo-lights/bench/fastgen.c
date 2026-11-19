// v3 빙고 전구 문제 생성기(C): bench/generate.py 의 judge · one_round · make 와 같은 보장을 훨씬 빠르게 찾는다.
//   clang -O2 -o fastgen fastgen.c
//   ./fastgen 버튼수 몫분자 몫분모 판수 씨앗 번호   → 문제 하나를 JSON 으로 stdout 에
// 판은 제출마다 바뀐다: 다음 판 = 직전 판에서 4 칸을 바꾸고 버튼 줄 순서를 섞은 것. 직전 판의 정답이 하나라도 통하면 버린다.
// 전환 라운드의 첫 판은 매우 드물어(후보의 약 0.002%), 못 찾으면 직전 라운드의 마지막 판을 다시 뽑아 출발점을 바꾼다.
// 결과는 파이썬 bench/verify.py 가 engine 으로 처음부터 다시 검사한다. 난수가 달라 파이썬 판과 같은 문제가 나오지는 않는다.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CELLS 9
#define NLINES 8
#define ROUNDS 5
#define CHANGE 4
#define MAXB 8
#define MAXSEQ 2048
#define WORDS (MAXSEQ / 64)
#define MAXBOARDS 20
enum { KEEP, ON, OFF };
static const char *EFFECT[] = {"KEEP", "ON", "OFF"};
static const int LINES[NLINES][3] = {{0, 1, 2}, {3, 4, 5}, {6, 7, 8}, {0, 3, 6}, {1, 4, 7}, {2, 5, 8}, {0, 4, 8}, {2, 4, 6}};

static int B, NSEQ, SNUM, SDEN, NBOARDS;
static long FIRST_TRIES = 20000, NEXT_TRIES = 5000, REROLLS = 100;
static int seq_len[MAXSEQ], seq_parent[MAXSEQ], seq_last[MAXSEQ], seq_b[MAXSEQ][4];
static uint8_t LINES_OF[512];
static int LINE_BITS[NLINES];

static uint64_t rng_s;
static uint64_t next64(void) {            // splitmix64
    uint64_t z = (rng_s += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}
static int rnd(int n) { return (int)(next64() % (uint64_t)n); }
static void shuffle(int *a, int n) {
    for (int i = n - 1; i > 0; i--) { int j = rnd(i + 1), t = a[i]; a[i] = a[j]; a[j] = t; }
}

typedef struct { int8_t e[MAXB][CELLS]; int on[MAXB], off[MAXB]; } Table;
typedef struct { int button, light, frm, to; } Change;
typedef struct { uint64_t w[WORDS]; } Set;
typedef struct { Table t; int shortest, dflt, rule, habit, has_from; Change ch[CHANGE]; int perm[MAXB]; } Board;
typedef struct { int goal, forbidden; Board b[MAXBOARDS]; Set S[MAXBOARDS]; } Round;

static Round rounds[ROUNDS];
static int plan_size, plan_kind[ROUNDS];  // kind: 0 뺌, 1 더함

static void masks(Table *t) {
    for (int b = 0; b < B; b++) {
        int on = 0, off = 0;
        for (int c = 0; c < CELLS; c++) { if (t->e[b][c] == ON) on |= 1 << c; else if (t->e[b][c] == OFF) off |= 1 << c; }
        t->on[b] = on; t->off[b] = off;
    }
}
static int distinct(const Table *t) {
    for (int a = 0; a < B; a++) for (int b = a + 1; b < B; b++) if (t->on[a] == t->on[b] && t->off[a] == t->off[b]) return 0;
    return 1;
}
static void random_table(Table *t) {      // 버튼마다 3~4 칸 켜고 0~2 칸 끈다
    memset(t, 0, sizeof *t);
    do {
        memset(t->e, KEEP, sizeof t->e);
        for (int b = 0; b < B; b++) {
            int cells[CELLS], on = 3 + rnd(2), off = rnd(3);
            for (int c = 0; c < CELLS; c++) cells[c] = c;
            shuffle(cells, CELLS);
            for (int k = 0; k < on + off; k++) t->e[b][cells[k]] = k < on ? ON : OFF;
        }
        masks(t);
    } while (!distinct(t));
}
static void perturb(const Table *from, Table *t, Change *ch) { // 정확히 CHANGE 칸. 버튼마다 켜는 칸 둘 이상, 버튼끼리 다르게
    for (;;) {
        *t = *from;
        int idx[MAXB * CELLS], n = B * CELLS;
        for (int i = 0; i < n; i++) idx[i] = i;
        for (int k = 0; k < CHANGE; k++) {
            int j = k + rnd(n - k), x = idx[j]; idx[j] = idx[k]; idx[k] = x;
            int b = x / CELLS, c = x % CELLS, cur = t->e[b][c], to = (cur + 1 + rnd(2)) % 3;
            ch[k] = (Change){b, c, cur, to};
            t->e[b][c] = (int8_t)to;
        }
        masks(t);
        int ok = 1;
        for (int b = 0; b < B && ok; b++) if (__builtin_popcount(t->on[b]) < 2) ok = 0;
        if (ok && distinct(t)) return;
    }
}
static void next_board(const Table *from, Table *t, Change *ch, int *perm) {  // 4 칸 바꾸고 버튼 줄 순서를 섞는다
    Table mid;
    perturb(from, &mid, ch);
    for (int b = 0; b < B; b++) perm[b] = b;
    shuffle(perm, B);
    memset(t, 0, sizeof *t);
    for (int b = 0; b < B; b++) memcpy(t->e[b], mid.e[perm[b]], CELLS);
    masks(t);
}

static int end_[MAXSEQ], lit_[MAXSEQ];
static uint8_t done_[MAXSEQ];
static void survey(const Table *t) {
    static int acc_end[MAXSEQ], acc_m[MAXSEQ];
    for (int i = 0; i < NSEQ; i++) {
        int b = seq_last[i], p = seq_parent[i];
        int e = p < 0 ? 0 : acc_end[p], m = p < 0 ? 0 : acc_m[p];
        e = (e | t->on[b]) & ~t->off[b]; m |= t->on[b];
        acc_end[i] = e; acc_m[i] = m;
        end_[i] = e; lit_[i] = m; done_[i] = LINES_OF[e];
    }
}
#define LIT(p, c) ((lit_[p] >> (c)) & 1)

static int want_bits(int goal) {
    int w = 0;
    for (int i = 0; i < NLINES; i++) if (goal >> i & 1) w |= LINE_BITS[i];
    return w;
}
static int lighting(const Table *t, int c) {
    int n = 0;
    for (int b = 0; b < B; b++) n += (t->on[b] >> c) & 1;
    return n;
}
static int blocked(const int *nl, int n, int old, int want) {
    int free_ = 0;
    for (int c = 0; c < CELLS; c++) if (!(want >> c & 1) && c != old) free_ |= 1 << c;
    int on_seen = 0, off_seen = 0, on_kept = 0, off_kept = 0;
    for (int i = 0; i < n; i++) {
        int e = end_[nl[i]];
        on_seen |= e; off_seen |= ~e & 511;
        if (!LIT(nl[i], old)) { on_kept |= e; off_kept |= ~e & 511; }
    }
    return ((on_seen & ~on_kept & free_) | (off_seen & ~off_kept & free_)) != 0;
}
static int disjoint(const Set *a, const Set *b) {
    if (!b) return 1;
    for (int w = 0; w < WORDS; w++) if (a->w[w] & b->w[w]) return 0;
    return 1;
}

// 라운드 d 의 (T, L) 이 이 판(survey 를 마친)에서 보장을 지키나. 지키면 bd 의 답들과 정답 집합 S 를 채운다.
static int judge(int d, const Table *t, int T, int L, Board *bd, Set *S) {
    const Round *prev = d ? &rounds[d - 1] : NULL;
    int want = want_bits(T);
    if (want >> L & 1) return 0;
    static int nl[MAXSEQ];
    int n = 0, minlen = 99;
    for (int i = 0; i < NSEQ; i++) if (done_[i] == T) { nl[n++] = i; if (seq_len[i] < minlen) minlen = seq_len[i]; }
    if (!n) return 0;
    int smin = 99, spick = -1, dpick = -1;
    memset(S, 0, sizeof *S);
    for (int i = 0; i < n; i++) {
        int p = nl[i];
        if (dpick < 0 && seq_len[p] == minlen) dpick = p;
        if (!LIT(p, L)) { S->w[p >> 6] |= 1ULL << (p & 63); if (seq_len[p] < smin) { smin = seq_len[p]; spick = p; } }
    }
    if (spick < 0 || smin > 3) return 0;
    bd->t = *t; bd->shortest = spick; bd->dflt = dpick; bd->rule = bd->habit = -1;
    if (!prev) {
        for (int i = 0; i < n; i++) if (seq_len[nl[i]] == minlen && !LIT(nl[i], L)) return 0;
        return smin <= minlen + 1;
    }
    int Lp = prev->forbidden, Tp = prev->goal;
    if (want >> Lp & 1) return 0;
    if (lighting(t, Lp) < 2) return 0;
    for (int i = 0; i < n; i++) if (seq_len[nl[i]] == minlen && (!LIT(nl[i], Lp) || LIT(nl[i], L))) return 0;
    int on_ = 0, keep = 0, hmin = 99, hpick = -1;
    for (int i = 0; i < NSEQ; i++) if (done_[i] == Tp) {
        if (seq_len[i] <= 3) { on_++; keep += !LIT(i, Lp); }
        if (!LIT(i, Lp) && seq_len[i] < hmin) { hmin = seq_len[i]; hpick = i; }
    }
    if (!on_ || keep * 2 < on_) return 0;                  // 옛 목표 3 번 이하 답의 절반 이상이 옛 금지를 피함
    if (hpick < 0 || hmin > 3) return 0;                   // 습관 답이 3 번 이하로 있음
    int ex = 0, kept = 0;
    for (int i = 0; i < n; i++) if (seq_len[nl[i]] <= 3) { ex++; kept += !LIT(nl[i], Lp); }
    if (kept * SDEN <= SNUM * ex) return 0;                // 새 목표 답의 몫이 SNUM / SDEN 을 넘음
    for (int i = 0; i < NSEQ; i++)                         // 가장 짧은 습관 답이 끝에 꺼진 목표 전구를 도중에 켜지 않음
        if (done_[i] == Tp && !LIT(i, Lp) && seq_len[i] == hmin && (lit_[i] & want & ~end_[i])) return 0;
    if (blocked(nl, n, Lp, want)) return 0;
    for (int i = 0; i < NSEQ; i++) if (done_[i] && !LIT(i, Lp) && !LIT(i, L)) return 0;   // 둘 다 피하면 줄 0
    int pmin = 99, ppick = -1;
    for (int i = 0; i < n; i++) if (!LIT(nl[i], Lp) && seq_len[nl[i]] < pmin) { pmin = seq_len[nl[i]]; ppick = nl[i]; }
    if (ppick < 0) return 0;
    bd->rule = ppick; bd->habit = hpick;
    return 1;
}

// (T, L) 과 무관한 전환 라운드 보장: 대부분의 판이 여기서 걸러진다.
static int board_ok(int d) {
    const Round *prev = &rounds[d - 1];
    int Lp = prev->forbidden, Tp = prev->goal, on_ = 0, keep = 0, hmin = 99;
    for (int i = 0; i < NSEQ; i++) if (done_[i] == Tp) {
        if (seq_len[i] <= 3) { on_++; keep += !LIT(i, Lp); }
        if (!LIT(i, Lp) && seq_len[i] < hmin) hmin = seq_len[i];
    }
    return on_ && keep * 2 >= on_ && hmin <= 3;
}

static void from_rec(Board *bd, const Change *ch, const int *perm) {
    bd->has_from = 1;
    memcpy(bd->ch, ch, sizeof(Change) * CHANGE);
    memcpy(bd->perm, perm, sizeof(int) * B);
}

static int extend(int d, const Table *from, int T, int L, const Set *lastS, Board *bd, Set *S) {
    int Lp = d ? rounds[d - 1].forbidden : -1;
    for (long k = 0; k < NEXT_TRIES; k++) {
        Table t; Change ch[CHANGE]; int perm[MAXB];
        next_board(from, &t, ch, perm);
        if (d && lighting(&t, Lp) < 2) continue;
        survey(&t);
        if (judge(d, &t, T, L, bd, S) && disjoint(S, lastS)) { from_rec(bd, ch, perm); return 1; }
    }
    return 0;
}

static int first_board(int d, const Table *start, const Set *prevS, int *T_, int *L_, Board *bd, Set *S) {
    const Round *prev = d ? &rounds[d - 1] : NULL;
    int Lp = prev ? prev->forbidden : -1, back = d >= 2 ? rounds[d - 2].forbidden : -1;
    long tries = start ? FIRST_TRIES : 100000;
    for (long k = 0; k < tries; k++) {
        Table t; Change ch[CHANGE]; int perm[MAXB];
        if (start) next_board(start, &t, ch, perm); else random_table(&t);
        if (prev && lighting(&t, Lp) < 2) continue;
        survey(&t);
        if (prev && !board_ok(d)) continue;
        int goals[64], ng = 0;
        if (prev) {
            for (int i = 0; i < NLINES; i++) {
                if (plan_kind[d] == 1 && !(prev->goal >> i & 1)) goals[ng++] = prev->goal | 1 << i;
                if (plan_kind[d] == 0 && (prev->goal >> i & 1)) goals[ng++] = prev->goal & ~(1 << i);
            }
        } else if (plan_size == 1) {
            for (int i = 0; i < NLINES; i++) goals[ng++] = 1 << i;
        } else {
            for (int i = 0; i < NLINES; i++) for (int j = i + 1; j < NLINES; j++) goals[ng++] = 1 << i | 1 << j;
        }
        shuffle(goals, ng);
        int lights[CELLS], nl_ = 0;
        for (int c = 0; c < CELLS; c++) if (c != Lp && c != back) lights[nl_++] = c;
        shuffle(lights, nl_);
        for (int g = 0; g < ng; g++) for (int l = 0; l < nl_; l++)
            if (judge(d, &t, goals[g], lights[l], bd, S) && disjoint(S, prevS)) {
                *T_ = goals[g]; *L_ = lights[l];
                if (start) from_rec(bd, ch, perm); else bd->has_from = 0;
                return 1;
            }
    }
    return 0;
}

static int one_round(int d, const Table *start, const Set *prevS) {
    Round *r = &rounds[d];
    for (int attempt = 0; attempt < 4; attempt++) {
        if (!first_board(d, start, prevS, &r->goal, &r->forbidden, &r->b[0], &r->S[0])) return 0;
        int j = 1;
        while (j < NBOARDS && extend(d, &r->b[j - 1].t, r->goal, r->forbidden, &r->S[j - 1], &r->b[j], &r->S[j])) j++;
        if (j == NBOARDS) return 1;
    }
    return 0;
}

static int make(void) {
    if (!one_round(0, NULL, NULL)) return 0;
    for (int d = 1; d < ROUNDS; d++) {
        Round *p = &rounds[d - 1];
        int ok = 0;
        for (long again = 0; again < REROLLS && !ok; again++) {
            if (again) {                                   // 직전 라운드 마지막 판을 다시 뽑아 출발점을 바꾼다
                Board nb; Set nS;
                if (!extend(d - 1, &p->b[NBOARDS - 2].t, p->goal, p->forbidden, &p->S[NBOARDS - 2], &nb, &nS)) continue;
                p->b[NBOARDS - 1] = nb; p->S[NBOARDS - 1] = nS;
            }
            ok = one_round(d, &p->b[NBOARDS - 1].t, &p->S[NBOARDS - 1]);
        }
        if (!ok) return 0;
    }
    return 1;
}

static void seq_json(int i) {
    putchar('[');
    for (int k = 0; k < seq_len[i]; k++) printf("%s%d", k ? ", " : "", seq_b[i][k]);
    putchar(']');
}
static void print_problem(void) {
    printf("{\"rounds\": [");
    for (int d = 0; d < ROUNDS; d++) {
        const Round *r = &rounds[d];
        printf("%s{\"round\": %d, \"goal\": [", d ? ", " : "", d + 1);
        int first = 1;
        for (int i = 0; i < NLINES; i++) if (r->goal >> i & 1) { printf("%s%d", first ? "" : ", ", i); first = 0; }
        printf("], \"forbidden\": %d, \"boards\": [", r->forbidden);
        for (int j = 0; j < NBOARDS; j++) {
            const Board *bd = &r->b[j];
            printf("%s{\"table\": [", j ? ", " : "");
            for (int b = 0; b < B; b++) {
                printf("%s[", b ? ", " : "");
                for (int c = 0; c < CELLS; c++) printf("%s\"%s\"", c ? ", " : "", EFFECT[(int)bd->t.e[b][c]]);
                putchar(']');
            }
            printf("], \"shortest\": "); seq_json(bd->shortest);
            printf(", \"default\": "); seq_json(bd->dflt);
            if (d) { printf(", \"ruleStick\": "); seq_json(bd->rule); printf(", \"habit\": "); seq_json(bd->habit); }
            if (bd->has_from) {
                printf(", \"from\": {\"change\": [");
                for (int k = 0; k < CHANGE; k++)
                    printf("%s{\"button\": %d, \"light\": %d, \"frm\": \"%s\", \"to\": \"%s\"}", k ? ", " : "",
                           bd->ch[k].button, bd->ch[k].light, EFFECT[bd->ch[k].frm], EFFECT[bd->ch[k].to]);
                printf("], \"perm\": [");
                for (int b = 0; b < B; b++) printf("%s%d", b ? ", " : "", bd->perm[b]);
                printf("]}");
            }
            putchar('}');
        }
        printf("]}");
    }
    printf("]}\n");
}

static void make_seqs(void) {             // engine.sequences 와 같은 순서: 길이순, 같은 길이는 글자순
    NSEQ = 0;
    for (int len = 1; len <= 4; len++) {
        int a[4];
        for (a[0] = 0; a[0] < B; a[0]++) {
            if (len == 1) { seq_len[NSEQ] = 1; seq_b[NSEQ][0] = a[0]; NSEQ++; continue; }
            for (a[1] = 0; a[1] < B; a[1]++) {
                if (a[1] == a[0]) continue;
                if (len == 2) { seq_len[NSEQ] = 2; memcpy(seq_b[NSEQ], a, sizeof(int) * 2); NSEQ++; continue; }
                for (a[2] = 0; a[2] < B; a[2]++) {
                    if (a[2] == a[0] || a[2] == a[1]) continue;
                    if (len == 3) { seq_len[NSEQ] = 3; memcpy(seq_b[NSEQ], a, sizeof(int) * 3); NSEQ++; continue; }
                    for (a[3] = 0; a[3] < B; a[3]++) {
                        if (a[3] == a[0] || a[3] == a[1] || a[3] == a[2]) continue;
                        seq_len[NSEQ] = 4; memcpy(seq_b[NSEQ], a, sizeof(int) * 4); NSEQ++;
                    }
                }
            }
        }
    }
    for (int i = 0; i < NSEQ; i++) {
        seq_last[i] = seq_b[i][seq_len[i] - 1];
        seq_parent[i] = -1;
        if (seq_len[i] > 1)
            for (int j = 0; j < NSEQ; j++)
                if (seq_len[j] == seq_len[i] - 1 && !memcmp(seq_b[j], seq_b[i], sizeof(int) * seq_len[j])) { seq_parent[i] = j; break; }
    }
}

int main(int argc, char **argv) {
    if (argc < 7) { fprintf(stderr, "fastgen 버튼수 몫분자 몫분모 판수 씨앗 번호 [FIRST NEXT REROLLS]\n"); return 2; }
    B = atoi(argv[1]); SNUM = atoi(argv[2]); SDEN = atoi(argv[3]); NBOARDS = atoi(argv[4]);
    long long seed = atoll(argv[5]), index = atoll(argv[6]);
    if (argc >= 10) { FIRST_TRIES = atol(argv[7]); NEXT_TRIES = atol(argv[8]); REROLLS = atol(argv[9]); }
    if (NBOARDS < 2 || NBOARDS > MAXBOARDS || B > MAXB || SDEN <= 0) { fprintf(stderr, "인자 범위\n"); return 2; }
    rng_s = (uint64_t)(seed * 1000 + index);
    for (int i = 0; i < NLINES; i++) LINE_BITS[i] = 1 << LINES[i][0] | 1 << LINES[i][1] | 1 << LINES[i][2];
    for (int s = 0; s < 512; s++) {
        int m = 0;
        for (int i = 0; i < NLINES; i++) if ((s & LINE_BITS[i]) == LINE_BITS[i]) m |= 1 << i;
        LINES_OF[s] = (uint8_t)m;
    }
    make_seqs();
    int size = rnd(2) ? 1 : 2;             // plan: 줄 수가 한두 줄을 번갈아 오간다
    plan_size = size;
    for (int d = 1; d < ROUNDS; d++) { plan_kind[d] = size == 1; size = plan_kind[d] ? 2 : 1; }
    while (!make()) {}
    print_problem();
    return 0;
}
