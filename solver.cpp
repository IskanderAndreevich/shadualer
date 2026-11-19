/**
 * @file jackson_solver.cpp
 * @brief Computation core, instance generator, and CLI tool for the
 *        two-machine Jackson scheduling problem (J2 || C_max).
 *
 * Implements:
 *   - Schedule evaluation and Gantt chart block construction for J2 || C_max
 *     with all four job types (M1->M2, M2->M1, M1-only, M2-only).
 *   - Bellman-Johnson optimal permutation and its reversal (anti-optimal).
 *   - Reproducible 64-bit Mersenne Twister (mt19937_64) generator supporting
 *     the four interval schemes (Shafransky & Barketov, 2007) and deterministic
 *     realization modes (pairs, lower, upper, mid, random).
 *   - Clean blank-line-delimited .txt parser/serializer and JSON/text output.
 *   - Built-in self-verification test suite (--test).
 *
 * Standard: C++17 (no external dependencies).
 * Build example:
 *   g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic jackson_solver.cpp -o jackson_solver
 */

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace jackson {

// =============================================================================
// 1. Domain Model
// =============================================================================
namespace model {

struct TwoStageJob {
    std::size_t id = 0;           ///< 1-based job identifier.
    std::int64_t first_stage = 0; ///< First operation duration (>= 1).
    std::int64_t second_stage = 0;///< Second operation duration (>= 1).
};

struct ProblemInstance {
    std::vector<TwoStageJob> type1_jobs;     ///< Type 1: M1 -> M2 (a_j, b_j).
    std::vector<std::size_t> perm1;          ///< 0-based permutation for Type 1.
    std::vector<TwoStageJob> type2_jobs;     ///< Type 2: M2 -> M1 (c_j, d_j).
    std::vector<std::size_t> perm2;          ///< 0-based permutation for Type 2.
    std::vector<std::int64_t> type3_durations; ///< Type 3: M1 only (p_j).
    std::vector<std::int64_t> type4_durations; ///< Type 4: M2 only (q_j).
};

struct GanttBlock {
    int machine = 0;          ///< 1 for M1, 2 for M2.
    int job_type = 0;         ///< 1..4.
    std::size_t job_id = 0;   ///< 1-based job identifier within its type.
    std::int64_t start = 0;   ///< Operation start time.
    std::int64_t end = 0;     ///< Operation completion time.
};

struct MachineMetrics {
    std::int64_t initial_block_load = 0; ///< T1 (on M1) or T2 (on M2).
    std::int64_t total_work_time = 0;    ///< Total useful processing time (L1 / L2).
    std::int64_t completion_time = 0;    ///< Machine completion time (C^1 / C^2).
    std::int64_t internal_idle_time = 0; ///< Idle time before machine completion.
    std::int64_t total_idle_time = 0;    ///< Total idle time relative to C_max.
};

struct ScheduleResult {
    std::int64_t c_max = 0;
    std::int64_t opt_c_max = 0;
    std::int64_t anti_c_max = 0;
    MachineMetrics m1;
    MachineMetrics m2;
    std::vector<std::size_t> applied_perm1; ///< 0-based permutation actually used.
    std::vector<std::size_t> applied_perm2; ///< 0-based permutation actually used.
    std::vector<GanttBlock> blocks;
};

struct Interval {
    double lower = 0.0;
    double upper = 0.0;
};

enum class DurationMode {
    Pairs,  ///< Independent integer pairs U[min_val, max_val].
    Lower,  ///< Deterministic lower bound L of generated interval [L, U].
    Upper,  ///< Deterministic upper bound U of generated interval [L, U].
    Mid,    ///< Deterministic midpoint (L + U) / 2 of generated interval.
    Random  ///< Random realization inside [L, U].
};

enum class PermutationMode {
    Manual,
    Optimal,
    AntiOptimal
};

enum class OutputFormat {
    Text,
    Json
};

struct GeneratorConfig {
    std::size_t n1 = 5;
    std::size_t n2 = 1;
    std::size_t n3 = 0;
    std::size_t n4 = 0;
    DurationMode mode = DurationMode::Random;
    int interval_method = 1; ///< 1..4 (Shafransky & Barketov, 2007).
    double min_val = 20.0;
    double max_val = 1000.0;
    double beta = 0.3;
    double alpha = 1.5;
    std::uint64_t seed = 0;  ///< 0 selects a random 64-bit seed.
};

} // namespace model

// =============================================================================
// 2. Scheduling Algorithms
// =============================================================================
namespace algo {

using namespace model;

/**
 * @brief Validates that perm is a bijection of {0, ..., n - 1}.
 */
void validate_permutation(
    const std::vector<std::size_t>& perm,
    std::size_t n,
    std::string_view label
) {
    if (perm.size() != n) {
        throw std::invalid_argument(
            "Invalid permutation " + std::string(label) + ": expected length " +
            std::to_string(n) + ", got " + std::to_string(perm.size()) + "."
        );
    }
    std::vector<bool> seen(n, false);
    for (const std::size_t idx : perm) {
        if (idx >= n || seen[idx]) {
            throw std::invalid_argument(
                "Invalid permutation " + std::string(label) +
                ": must contain each index from 1 to " + std::to_string(n) + " exactly once."
            );
        }
        seen[idx] = true;
    }
}

/**
 * @brief Validates all durations, job counts, and permutations in an instance.
 */
void validate_instance(const ProblemInstance& instance) {
    const std::size_t total_jobs =
        instance.type1_jobs.size() + instance.type2_jobs.size() +
        instance.type3_durations.size() + instance.type4_durations.size();

    if (total_jobs == 0) {
        throw std::invalid_argument("Problem instance must contain at least one job.");
    }

    for (const auto& j : instance.type1_jobs) {
        if (j.first_stage < 1 || j.second_stage < 1) {
            throw std::invalid_argument("Type 1 durations must be positive integers (>= 1).");
        }
    }
    for (const auto& j : instance.type2_jobs) {
        if (j.first_stage < 1 || j.second_stage < 1) {
            throw std::invalid_argument("Type 2 durations must be positive integers (>= 1).");
        }
    }
    for (const std::int64_t p : instance.type3_durations) {
        if (p < 1) {
            throw std::invalid_argument("Type 3 durations must be positive integers (>= 1).");
        }
    }
    for (const std::int64_t q : instance.type4_durations) {
        if (q < 1) {
            throw std::invalid_argument("Type 4 durations must be positive integers (>= 1).");
        }
    }

    validate_permutation(instance.perm1, instance.type1_jobs.size(), "pi_1");
    validate_permutation(instance.perm2, instance.type2_jobs.size(), "pi_2");
}

/**
 * @brief Constructs the Bellman-Johnson optimal sequence of two-stage jobs.
 * @return 0-based job indices ordered by Johnson's rule.
 */
[[nodiscard]] std::vector<std::size_t> build_bellman_johnson_perm(
    const std::vector<TwoStageJob>& jobs
) {
    std::vector<std::size_t> group1;
    std::vector<std::size_t> group2;
    group1.reserve(jobs.size());
    group2.reserve(jobs.size());

    for (std::size_t i = 0; i < jobs.size(); ++i) {
        if (jobs[i].first_stage <= jobs[i].second_stage) {
            group1.push_back(i);
        } else {
            group2.push_back(i);
        }
    }

    std::sort(group1.begin(), group1.end(), [&jobs](std::size_t lhs, std::size_t rhs) {
        if (jobs[lhs].first_stage != jobs[rhs].first_stage) {
            return jobs[lhs].first_stage < jobs[rhs].first_stage;
        }
        if (jobs[lhs].second_stage != jobs[rhs].second_stage) {
            return jobs[lhs].second_stage > jobs[rhs].second_stage;
        }
        return jobs[lhs].id < jobs[rhs].id;
    });

    std::sort(group2.begin(), group2.end(), [&jobs](std::size_t lhs, std::size_t rhs) {
        if (jobs[lhs].second_stage != jobs[rhs].second_stage) {
            return jobs[lhs].second_stage > jobs[rhs].second_stage;
        }
        if (jobs[lhs].first_stage != jobs[rhs].first_stage) {
            return jobs[lhs].first_stage < jobs[rhs].first_stage;
        }
        return jobs[lhs].id < jobs[rhs].id;
    });

    group1.insert(group1.end(), group2.begin(), group2.end());
    return group1;
}

/**
 * @brief Constructs the anti-optimal sequence as the reverse of Bellman-Johnson.
 */
[[nodiscard]] std::vector<std::size_t> build_anti_optimal_perm(
    const std::vector<TwoStageJob>& jobs
) {
    std::vector<std::size_t> perm = build_bellman_johnson_perm(jobs);
    std::reverse(perm.begin(), perm.end());
    return perm;
}

/**
 * @brief Computes the standalone two-machine flow-shop makespan F(perm).
 */
[[nodiscard]] std::int64_t compute_flowshop_makespan(
    const std::vector<TwoStageJob>& jobs,
    const std::vector<std::size_t>& perm
) {
    std::int64_t t1 = 0;
    std::int64_t t2 = 0;
    for (const std::size_t idx : perm) {
        t1 += jobs[idx].first_stage;
        t2 = std::max(t1, t2) + jobs[idx].second_stage;
    }
    return t2;
}

/**
 * @brief Computes C_max via the analytical identity max(L1, L2, F1(pi1), F2(pi2)).
 */
[[nodiscard]] std::int64_t compute_cmax_identity(
    const ProblemInstance& instance,
    const std::vector<std::size_t>& perm1,
    const std::vector<std::size_t>& perm2
) {
    std::int64_t sum_a = 0;
    std::int64_t sum_b = 0;
    std::int64_t sum_c = 0;
    std::int64_t sum_d = 0;
    std::int64_t sum_p = 0;
    std::int64_t sum_q = 0;

    for (const auto& j : instance.type1_jobs) {
        sum_a += j.first_stage;
        sum_b += j.second_stage;
    }
    for (const auto& j : instance.type2_jobs) {
        sum_c += j.first_stage;
        sum_d += j.second_stage;
    }
    for (const std::int64_t p : instance.type3_durations) {
        sum_p += p;
    }
    for (const std::int64_t q : instance.type4_durations) {
        sum_q += q;
    }

    const std::int64_t load1 = sum_a + sum_p + sum_d;
    const std::int64_t load2 = sum_b + sum_q + sum_c;
    const std::int64_t f1 = compute_flowshop_makespan(instance.type1_jobs, perm1);
    const std::int64_t f2 = compute_flowshop_makespan(instance.type2_jobs, perm2);

    return std::max({load1, load2, f1, f2});
}

namespace detail {

[[nodiscard]] std::int64_t append_initial_blocks(
    int machine_id,
    int two_stage_type,
    const std::vector<TwoStageJob>& two_stage_jobs,
    const std::vector<std::size_t>& perm,
    int single_stage_type,
    const std::vector<std::int64_t>& single_stage_durations,
    std::vector<GanttBlock>& blocks
) {
    std::int64_t current_time = 0;
    for (const std::size_t idx : perm) {
        const std::int64_t dur = two_stage_jobs[idx].first_stage;
        blocks.push_back(GanttBlock{
            machine_id,
            two_stage_type,
            two_stage_jobs[idx].id,
            current_time,
            current_time + dur
        });
        current_time += dur;
    }
    for (std::size_t i = 0; i < single_stage_durations.size(); ++i) {
        const std::int64_t dur = single_stage_durations[i];
        blocks.push_back(GanttBlock{
            machine_id,
            single_stage_type,
            i + 1,
            current_time,
            current_time + dur
        });
        current_time += dur;
    }
    return current_time;
}

[[nodiscard]] MachineMetrics evaluate_conveyor_tail(
    int machine_id,
    int tail_job_type,
    std::int64_t initial_load,
    const std::vector<TwoStageJob>& jobs,
    const std::vector<std::size_t>& perm,
    std::vector<GanttBlock>& blocks
) {
    std::int64_t prev_machine_ready = 0;
    std::int64_t curr_machine_free = initial_load;
    std::int64_t tail_work_duration = 0;

    for (const std::size_t idx : perm) {
        prev_machine_ready += jobs[idx].first_stage;
        const std::int64_t start = std::max(curr_machine_free, prev_machine_ready);
        const std::int64_t dur = jobs[idx].second_stage;
        const std::int64_t end = start + dur;

        blocks.push_back(GanttBlock{
            machine_id,
            tail_job_type,
            jobs[idx].id,
            start,
            end
        });

        curr_machine_free = end;
        tail_work_duration += dur;
    }

    const std::int64_t total_work = initial_load + tail_work_duration;
    const std::int64_t completion = curr_machine_free;
    const std::int64_t internal_idle = std::max<std::int64_t>(0, completion - total_work);

    return MachineMetrics{
        initial_load,
        total_work,
        completion,
        internal_idle,
        0
    };
}

} // namespace detail

/**
 * @brief Evaluates the Jackson schedule, machine metrics, and Gantt blocks.
 */
[[nodiscard]] ScheduleResult evaluate_schedule(
    const ProblemInstance& instance,
    PermutationMode mode = PermutationMode::Manual
) {
    validate_instance(instance);

    const std::vector<std::size_t> opt_perm1 = build_bellman_johnson_perm(instance.type1_jobs);
    const std::vector<std::size_t> opt_perm2 = build_bellman_johnson_perm(instance.type2_jobs);

    std::vector<std::size_t> anti_perm1 = opt_perm1;
    std::reverse(anti_perm1.begin(), anti_perm1.end());
    std::vector<std::size_t> anti_perm2 = opt_perm2;
    std::reverse(anti_perm2.begin(), anti_perm2.end());

    const std::vector<std::size_t>* active_perm1 = &instance.perm1;
    const std::vector<std::size_t>* active_perm2 = &instance.perm2;

    if (mode == PermutationMode::Optimal) {
        active_perm1 = &opt_perm1;
        active_perm2 = &opt_perm2;
    } else if (mode == PermutationMode::AntiOptimal) {
        active_perm1 = &anti_perm1;
        active_perm2 = &anti_perm2;
    }

    ScheduleResult result;
    result.applied_perm1 = *active_perm1;
    result.applied_perm2 = *active_perm2;
    result.blocks.reserve(
        2 * (instance.type1_jobs.size() + instance.type2_jobs.size()) +
        instance.type3_durations.size() + instance.type4_durations.size()
    );

    const std::int64_t t1_load = detail::append_initial_blocks(
        1, 1, instance.type1_jobs, *active_perm1,
        3, instance.type3_durations, result.blocks
    );
    const std::int64_t t2_load = detail::append_initial_blocks(
        2, 2, instance.type2_jobs, *active_perm2,
        4, instance.type4_durations, result.blocks
    );

    result.m1 = detail::evaluate_conveyor_tail(
        1, 2, t1_load, instance.type2_jobs, *active_perm2, result.blocks
    );
    result.m2 = detail::evaluate_conveyor_tail(
        2, 1, t2_load, instance.type1_jobs, *active_perm1, result.blocks
    );

    result.c_max = std::max(result.m1.completion_time, result.m2.completion_time);
    result.m1.total_idle_time = std::max<std::int64_t>(0, result.c_max - result.m1.total_work_time);
    result.m2.total_idle_time = std::max<std::int64_t>(0, result.c_max - result.m2.total_work_time);

    result.opt_c_max = compute_cmax_identity(instance, opt_perm1, opt_perm2);
    result.anti_c_max = compute_cmax_identity(instance, anti_perm1, anti_perm2);

    return result;
}

} // namespace algo

// =============================================================================
// 3. Reproducible Instance Generator (mt19937_64)
// =============================================================================
namespace gen {

using namespace model;

/**
 * @brief Cross-platform 53-bit IEEE-754 uniform draw in [0, 1).
 *
 * Guarantees bit-identical sequences across GCC, Clang, MSVC, and JavaScript
 * when initialized with the same 64-bit seed.
 */
[[nodiscard]] inline double uniform_unit_53bit(std::mt19937_64& rng) noexcept {
    constexpr double kInvTwoPow53 = 1.0 / 9007199254740992.0; // 2^-53
    return static_cast<double>(rng() >> 11) * kInvTwoPow53;
}

[[nodiscard]] inline double sample_real(
    std::mt19937_64& rng,
    double low,
    double high
) noexcept {
    if (high <= low) {
        return low;
    }
    return low + uniform_unit_53bit(rng) * (high - low);
}

[[nodiscard]] std::uint64_t make_random_seed() {
    std::random_device rd;
    const std::uint64_t hi = static_cast<std::uint64_t>(rd());
    const std::uint64_t lo = static_cast<std::uint64_t>(rd());
    const std::uint64_t seed = (hi << 32) ^ lo;
    return seed == 0 ? 1ULL : seed;
}

void validate_generator_config(const GeneratorConfig& cfg) {
    if (cfg.n1 + cfg.n2 + cfg.n3 + cfg.n4 == 0) {
        throw std::invalid_argument("Total number of jobs (n1 + n2 + n3 + n4) must be > 0.");
    }
    if (cfg.min_val < 1.0 || cfg.max_val < cfg.min_val) {
        throw std::invalid_argument("Generator range must satisfy 1 <= min_val <= max_val.");
    }
    if (cfg.mode != DurationMode::Pairs) {
        if (cfg.interval_method < 1 || cfg.interval_method > 4) {
            throw std::invalid_argument("Interval generation method must be in [1, 4].");
        }
        if (cfg.beta < 0.0 || cfg.beta > 1.0) {
            throw std::invalid_argument("Parameter beta must lie in [0, 1].");
        }
        if (cfg.interval_method == 4 && cfg.alpha <= 1.0) {
            throw std::invalid_argument("Parameter alpha must be strictly greater than 1 for Method 4.");
        }
    }
}

/**
 * @brief Step 1: Generates an uncertainty interval [L, U] according to
 *        Methods 1..4 from Shafransky & Barketov (2007).
 */
[[nodiscard]] Interval generate_interval(
    std::mt19937_64& rng,
    int method,
    double min_val,
    double max_val,
    double beta,
    double alpha
) noexcept {
    if (method == 1) {
        const double x1 = sample_real(rng, min_val, max_val);
        const double x2 = sample_real(rng, min_val, max_val);
        return Interval{std::min(x1, x2), std::max(x1, x2)};
    }

    const double lower = sample_real(rng, min_val, max_val);
    double upper = max_val;

    if (method == 2) {
        upper = sample_real(rng, lower + beta * (max_val - lower), max_val);
    } else if (method == 3) {
        const double u_lo = std::min(lower + 100.0 * beta, max_val);
        const double u_hi = std::min(lower + 100.0, max_val);
        upper = sample_real(rng, u_lo, u_hi);
    } else if (method == 4) {
        const double u_lo = std::min(lower + beta * lower * (alpha - 1.0), max_val);
        const double u_hi = std::min(alpha * lower, max_val);
        upper = sample_real(rng, u_lo, u_hi);
    }
    return Interval{lower, upper};
}

/**
 * @brief Step 2: Reduces an interval [L, U] (or direct range) to a positive integer duration.
 */
[[nodiscard]] std::int64_t generate_duration(
    std::mt19937_64& rng,
    const GeneratorConfig& cfg
) noexcept {
    double raw = cfg.min_val;
    if (cfg.mode == DurationMode::Pairs) {
        raw = sample_real(rng, cfg.min_val, cfg.max_val);
    } else {
        const Interval iv = generate_interval(
            rng, cfg.interval_method, cfg.min_val, cfg.max_val, cfg.beta, cfg.alpha
        );
        switch (cfg.mode) {
            case DurationMode::Lower:
                raw = iv.lower;
                break;
            case DurationMode::Upper:
                raw = iv.upper;
                break;
            case DurationMode::Mid:
                raw = 0.5 * (iv.lower + iv.upper);
                break;
            case DurationMode::Random:
                raw = sample_real(rng, iv.lower, iv.upper);
                break;
            case DurationMode::Pairs:
                break;
        }
    }
    const std::int64_t rounded = static_cast<std::int64_t>(std::llround(raw));
    return std::max<std::int64_t>(1, rounded);
}

/**
 * @brief Generates a random 0-based permutation via Fisher-Yates shuffle.
 */
[[nodiscard]] std::vector<std::size_t> generate_random_permutation(
    std::size_t n,
    std::mt19937_64& rng
) {
    std::vector<std::size_t> perm(n);
    for (std::size_t i = 0; i < n; ++i) {
        perm[i] = i;
    }
    for (std::size_t i = n; i > 1; --i) {
        const std::size_t j = static_cast<std::size_t>(
            rng() % static_cast<std::uint64_t>(i)
        );
        std::swap(perm[i - 1], perm[j]);
    }
    return perm;
}

/**
 * @brief Generates a complete J2 || C_max problem instance and returns the seed used.
 */
[[nodiscard]] ProblemInstance generate_instance(
    const GeneratorConfig& cfg,
    std::uint64_t& actual_seed_out
) {
    validate_generator_config(cfg);

    actual_seed_out = (cfg.seed == 0) ? make_random_seed() : cfg.seed;
    std::mt19937_64 rng(actual_seed_out);

    ProblemInstance instance;
    instance.type1_jobs.reserve(cfg.n1);
    instance.type2_jobs.reserve(cfg.n2);
    instance.type3_durations.reserve(cfg.n3);
    instance.type4_durations.reserve(cfg.n4);

    for (std::size_t i = 0; i < cfg.n1; ++i) {
        const std::int64_t a = generate_duration(rng, cfg);
        const std::int64_t b = generate_duration(rng, cfg);
        instance.type1_jobs.push_back(TwoStageJob{i + 1, a, b});
    }
    for (std::size_t i = 0; i < cfg.n2; ++i) {
        const std::int64_t c = generate_duration(rng, cfg);
        const std::int64_t d = generate_duration(rng, cfg);
        instance.type2_jobs.push_back(TwoStageJob{i + 1, c, d});
    }
    for (std::size_t i = 0; i < cfg.n3; ++i) {
        instance.type3_durations.push_back(generate_duration(rng, cfg));
    }
    for (std::size_t i = 0; i < cfg.n4; ++i) {
        instance.type4_durations.push_back(generate_duration(rng, cfg));
    }

    instance.perm1 = generate_random_permutation(cfg.n1, rng);
    instance.perm2 = generate_random_permutation(cfg.n2, rng);

    return instance;
}

} // namespace gen

// =============================================================================
// 4. Clean .txt Parser / Serializer & Result Formatters
// =============================================================================
namespace io {

using namespace model;

namespace detail {

[[nodiscard]] std::string trim(std::string_view sv) {
    std::size_t first = 0;
    while (first < sv.size() && std::isspace(static_cast<unsigned char>(sv[first]))) {
        ++first;
    }
    std::size_t last = sv.size();
    while (last > first && std::isspace(static_cast<unsigned char>(sv[last - 1]))) {
        --last;
    }
    return std::string(sv.substr(first, last - first));
}

[[nodiscard]] std::string normalize_newlines_and_bom(std::string text) {
    // Strip UTF-8 BOM if saved by Windows Notepad.
    if (text.size() >= 3 &&
        static_cast<unsigned char>(text[0]) == 0xEF &&
        static_cast<unsigned char>(text[1]) == 0xBB &&
        static_cast<unsigned char>(text[2]) == 0xBF) {
        text.erase(0, 3);
    }

    std::string out;
    out.reserve(text.size());
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\r') {
            if (i + 1 < text.size() && text[i + 1] == '\n') {
                ++i;
            }
            out.push_back('\n');
        } else {
            out.push_back(text[i]);
        }
    }
    return out;
}

[[nodiscard]] std::int64_t parse_positive_int(
    std::string_view token,
    std::string_view context
) {
    if (token.empty()) {
        throw std::invalid_argument("Empty token in " + std::string(context) + ".");
    }
    for (const char ch : token) {
        if (!std::isdigit(static_cast<unsigned char>(ch))) {
            throw std::invalid_argument(
                "Expected positive integer (>= 1) in " + std::string(context) +
                ", got '" + std::string(token) + "'."
            );
        }
    }
    try {
        const std::int64_t value = std::stoll(std::string(token));
        if (value < 1) {
            throw std::invalid_argument(
                "Duration/index must be >= 1 in " + std::string(context) +
                ", got '" + std::string(token) + "'."
            );
        }
        return value;
    } catch (const std::out_of_range&) {
        throw std::invalid_argument(
            "Integer overflow in " + std::string(context) +
            " for token '" + std::string(token) + "'."
        );
    }
}

[[nodiscard]] std::vector<std::int64_t> extract_numbers(
    const std::string& block,
    std::string_view label
) {
    const std::string cleaned = trim(block);
    if (cleaned.empty() || cleaned == "-") {
        return {};
    }

    std::string normalized = cleaned;
    std::replace(normalized.begin(), normalized.end(), ',', ' ');

    std::istringstream iss(normalized);
    std::vector<std::int64_t> values;
    std::string token;
    while (iss >> token) {
        values.push_back(parse_positive_int(token, label));
    }
    return values;
}

[[nodiscard]] std::vector<std::string> split_into_blocks(const std::string& raw_text) {
    const std::string text = trim(normalize_newlines_and_bom(raw_text));
    if (text.empty()) {
        throw std::invalid_argument("Input text/file is empty.");
    }

    std::vector<std::string> lines;
    {
        std::istringstream iss(text);
        std::string line;
        while (std::getline(iss, line)) {
            lines.push_back(line);
        }
    }

    std::vector<std::string> blocks;
    std::string current;
    bool has_blank_line = false;

    for (const std::string& line : lines) {
        if (trim(line).empty()) {
            has_blank_line = true;
            if (!current.empty()) {
                blocks.push_back(current);
                current.clear();
            }
        } else {
            if (!current.empty()) {
                current.push_back('\n');
            }
            current += line;
        }
    }
    if (!current.empty()) {
        blocks.push_back(current);
    }

    // Fallback: compact format (no blank lines, 1 line per logical block).
    if (!has_blank_line && lines.size() > 1) {
        blocks.clear();
        for (const std::string& line : lines) {
            const std::string t = trim(line);
            if (!t.empty()) {
                blocks.push_back(t);
            }
        }
    }

    return blocks;
}

[[nodiscard]] std::vector<TwoStageJob> build_two_stage_jobs(
    const std::vector<std::int64_t>& nums,
    std::string_view label
) {
    if (nums.size() % 2 != 0) {
        throw std::invalid_argument(
            "Odd number of durations (" + std::to_string(nums.size()) +
            ") in block '" + std::string(label) + "': pairs (op1 op2) expected."
        );
    }
    std::vector<TwoStageJob> jobs;
    jobs.reserve(nums.size() / 2);
    for (std::size_t i = 0; i < nums.size(); i += 2) {
        jobs.push_back(TwoStageJob{i / 2 + 1, nums[i], nums[i + 1]});
    }
    return jobs;
}

[[nodiscard]] std::vector<std::size_t> build_zero_based_perm(
    const std::vector<std::int64_t>& nums,
    std::size_t n,
    std::string_view label
) {
    if (n == 0) {
        if (!nums.empty()) {
            throw std::invalid_argument(
                "Permutation " + std::string(label) + " provided for 0 jobs."
            );
        }
        return {};
    }
    if (nums.empty()) {
        std::vector<std::size_t> identity(n);
        for (std::size_t i = 0; i < n; ++i) {
            identity[i] = i;
        }
        return identity;
    }

    std::vector<std::size_t> perm;
    perm.reserve(nums.size());
    for (const std::int64_t v : nums) {
        perm.push_back(static_cast<std::size_t>(v - 1));
    }
    algo::validate_permutation(perm, n, label);
    return perm;
}

} // namespace detail

/**
 * @brief Parses a ProblemInstance from the clean blank-line-delimited .txt format.
 *
 * Block order:
 *   1. Type 1 jobs (pairs a_j b_j, in columns or inline)
 *   2. Permutation pi_1 (1-based, or '-' for natural order 1..n1)
 *   3. Type 2 jobs (pairs c_j d_j, or '-' if absent)
 *   4. Permutation pi_2 (1-based, or '-' for natural order 1..n2)
 *   5. Type 3 jobs p_j (optional, or '-')
 *   6. Type 4 jobs q_j (optional, or '-')
 */
[[nodiscard]] ProblemInstance parse_problem_text(const std::string& raw_text) {
    const std::vector<std::string> blocks = detail::split_into_blocks(raw_text);
    auto get_block = [&blocks](std::size_t idx) -> std::string {
        return idx < blocks.size() ? blocks[idx] : std::string{};
    };

    const auto b1 = detail::extract_numbers(get_block(0), "Type 1");
    const auto b2 = detail::extract_numbers(get_block(1), "Permutation pi_1");
    const auto b3 = detail::extract_numbers(get_block(2), "Type 2");
    const auto b4 = detail::extract_numbers(get_block(3), "Permutation pi_2");
    const auto b5 = detail::extract_numbers(get_block(4), "Type 3");
    const auto b6 = detail::extract_numbers(get_block(5), "Type 4");

    ProblemInstance instance;
    instance.type1_jobs = detail::build_two_stage_jobs(b1, "Type 1");
    instance.type2_jobs = detail::build_two_stage_jobs(b3, "Type 2");
    instance.perm1 = detail::build_zero_based_perm(b2, instance.type1_jobs.size(), "pi_1");
    instance.perm2 = detail::build_zero_based_perm(b4, instance.type2_jobs.size(), "pi_2");
    instance.type3_durations = b5;
    instance.type4_durations = b6;

    algo::validate_instance(instance);
    return instance;
}

[[nodiscard]] ProblemInstance load_problem_file(const std::string& filepath) {
    std::ifstream fin(filepath);
    if (!fin.is_open()) {
        throw std::runtime_error("Cannot open input file: " + filepath);
    }
    std::ostringstream oss;
    oss << fin.rdbuf();
    return parse_problem_text(oss.str());
}

/**
 * @brief Serializes a ProblemInstance to the clean blank-line-delimited .txt format.
 */
[[nodiscard]] std::string serialize_problem_text(const ProblemInstance& instance) {
    algo::validate_instance(instance);

    auto format_jobs = [](const std::vector<TwoStageJob>& jobs) -> std::string {
        if (jobs.empty()) {
            return "-";
        }
        std::ostringstream oss;
        for (std::size_t i = 0; i < jobs.size(); ++i) {
            if (i > 0) {
                oss << '\n';
            }
            oss << jobs[i].first_stage << ' ' << jobs[i].second_stage;
        }
        return oss.str();
    };

    auto format_perm = [](const std::vector<std::size_t>& perm) -> std::string {
        if (perm.empty()) {
            return "-";
        }
        std::ostringstream oss;
        for (std::size_t i = 0; i < perm.size(); ++i) {
            if (i > 0) {
                oss << ' ';
            }
            oss << (perm[i] + 1);
        }
        return oss.str();
    };

    auto format_single = [](const std::vector<std::int64_t>& durs) -> std::string {
        if (durs.empty()) {
            return "-";
        }
        std::ostringstream oss;
        for (std::size_t i = 0; i < durs.size(); ++i) {
            if (i > 0) {
                oss << ' ';
            }
            oss << durs[i];
        }
        return oss.str();
    };

    std::vector<std::string> blocks = {
        format_jobs(instance.type1_jobs),
        format_perm(instance.perm1),
        format_jobs(instance.type2_jobs),
        format_perm(instance.perm2)
    };

    if (!instance.type3_durations.empty() || !instance.type4_durations.empty()) {
        blocks.push_back(format_single(instance.type3_durations));
    }
    if (!instance.type4_durations.empty()) {
        blocks.push_back(format_single(instance.type4_durations));
    }

    std::ostringstream out;
    for (std::size_t i = 0; i < blocks.size(); ++i) {
        if (i > 0) {
            out << "\n\n";
        }
        out << blocks[i];
    }
    out << '\n';
    return out.str();
}

void save_problem_file(const std::string& filepath, const ProblemInstance& instance) {
    std::ofstream fout(filepath);
    if (!fout.is_open()) {
        throw std::runtime_error("Cannot open output file for writing: " + filepath);
    }
    fout << serialize_problem_text(instance);
}

[[nodiscard]] std::string format_result_text(const ScheduleResult& res) {
    auto join_perm = [](const std::vector<std::size_t>& perm) -> std::string {
        if (perm.empty()) {
            return "-";
        }
        std::ostringstream oss;
        for (std::size_t i = 0; i < perm.size(); ++i) {
            if (i > 0) {
                oss << ' ';
            }
            oss << (perm[i] + 1);
        }
        return oss.str();
    };

    std::ostringstream oss;
    oss << "C_max (current):       " << res.c_max << '\n'
        << "C_max (Bellman-John.): " << res.opt_c_max << '\n'
        << "C_max (anti-optimal):  " << res.anti_c_max << '\n'
        << "Applied pi_1:          " << join_perm(res.applied_perm1) << '\n'
        << "Applied pi_2:          " << join_perm(res.applied_perm2) << '\n'
        << "Machine 1 (M1):        T1=" << res.m1.initial_block_load
        << " | Work=" << res.m1.total_work_time
        << " | C1=" << res.m1.completion_time
        << " | InternalIdle=" << res.m1.internal_idle_time
        << " | TotalIdle=" << res.m1.total_idle_time << '\n'
        << "Machine 2 (M2):        T2=" << res.m2.initial_block_load
        << " | Work=" << res.m2.total_work_time
        << " | C2=" << res.m2.completion_time
        << " | InternalIdle=" << res.m2.internal_idle_time
        << " | TotalIdle=" << res.m2.total_idle_time << '\n';
    return oss.str();
}

[[nodiscard]] std::string format_result_json(const ScheduleResult& res) {
    auto write_perm_json = [](std::ostringstream& oss, const std::vector<std::size_t>& perm) {
        oss << '[';
        for (std::size_t i = 0; i < perm.size(); ++i) {
            if (i > 0) {
                oss << ',';
            }
            oss << (perm[i] + 1);
        }
        oss << ']';
    };

    auto write_metrics_json = [](std::ostringstream& oss, const MachineMetrics& m) {
        oss << "{\"initial_block_load\":" << m.initial_block_load
            << ",\"total_work_time\":" << m.total_work_time
            << ",\"completion_time\":" << m.completion_time
            << ",\"internal_idle_time\":" << m.internal_idle_time
            << ",\"total_idle_time\":" << m.total_idle_time << '}';
    };

    std::ostringstream oss;
    oss << "{\"c_max\":" << res.c_max
        << ",\"opt_c_max\":" << res.opt_c_max
        << ",\"anti_c_max\":" << res.anti_c_max
        << ",\"perm1\":";
    write_perm_json(oss, res.applied_perm1);
    oss << ",\"perm2\":";
    write_perm_json(oss, res.applied_perm2);
    oss << ",\"m1\":";
    write_metrics_json(oss, res.m1);
    oss << ",\"m2\":";
    write_metrics_json(oss, res.m2);
    oss << ",\"blocks\":[";
    for (std::size_t i = 0; i < res.blocks.size(); ++i) {
        if (i > 0) {
            oss << ',';
        }
        const auto& b = res.blocks[i];
        oss << "{\"machine\":" << b.machine
            << ",\"type\":" << b.job_type
            << ",\"id\":" << b.job_id
            << ",\"start\":" << b.start
            << ",\"end\":" << b.end << '}';
    }
    oss << "]}\n";
    return oss.str();
}

} // namespace io

// =============================================================================
// 5. Built-in Verification Suite (--test)
// =============================================================================
namespace tests {

using namespace model;

void expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error("Test failed: " + message);
    }
}

void run_all_tests() {
    // Test 1: Preset 1 (all 4 types) + round-trip serialization.
    {
        const std::string col_input =
            "3 6\n5 2\n4 5\n\n"
            "1 2 3\n\n"
            "4 5\n8 3\n2 6\n\n"
            "1 2 3\n\n"
            "3 2\n\n"
            "4\n";
        const ProblemInstance inst = io::parse_problem_text(col_input);
        const ScheduleResult res = algo::evaluate_schedule(inst, PermutationMode::Manual);
        const std::int64_t id_cmax = algo::compute_cmax_identity(inst, inst.perm1, inst.perm2);

        expect(res.c_max == id_cmax, "Preset 1: Gantt C_max must match identity max(L1,L2,F1,F2).");
        expect(res.opt_c_max <= res.c_max, "Preset 1: Optimal C_max must be <= manual C_max.");

        const std::string serialized = io::serialize_problem_text(inst);
        const ProblemInstance reparsed = io::parse_problem_text(serialized);
        const ScheduleResult res2 = algo::evaluate_schedule(reparsed, PermutationMode::Manual);
        expect(res.c_max == res2.c_max && res.blocks.size() == res2.blocks.size(),
               "Preset 1: Round-trip serialization mismatch.");
    }

    // Test 2: Inline comma format + UTF-8 BOM + dash skipping.
    {
        const std::string bom_inline =
            "\xEF\xBB\xBF"
            "4 3, 6 2, 5 4\n\n"
            "1 3 2\n\n"
            "-\n\n"
            "-\n";
        const ProblemInstance inst = io::parse_problem_text(bom_inline);
        expect(inst.type1_jobs.size() == 3 && inst.type2_jobs.empty(),
               "BOM/inline parser failed to parse Type 1 only.");
        const ScheduleResult res_opt = algo::evaluate_schedule(inst, PermutationMode::Optimal);
        expect(res_opt.c_max == res_opt.opt_c_max, "Optimal mode C_max mismatch.");
    }

    // Test 3: Compact 1-line-per-block fallback format.
    {
        const std::string compact =
            "3 6, 5 2\n"
            "2 1\n"
            "4 5\n"
            "1\n";
        const ProblemInstance inst = io::parse_problem_text(compact);
        expect(inst.type1_jobs.size() == 2 && inst.type2_jobs.size() == 1,
               "Compact format parsing failed.");
        expect(inst.perm1[0] == 1 && inst.perm1[1] == 0,
               "Compact format permutation mismatch.");
    }

    // Test 4: Invalid inputs must throw.
    {
        bool threw = false;
        try {
            (void)io::parse_problem_text("3 6 5\n\n1 2\n");
        } catch (const std::invalid_argument&) {
            threw = true;
        }
        expect(threw, "Odd count of Type 1 durations must be rejected.");

        threw = false;
        try {
            (void)io::parse_problem_text("3 0\n\n1\n");
        } catch (const std::invalid_argument&) {
            threw = true;
        }
        expect(threw, "Zero duration must be rejected.");
    }

    // Test 5: Reproducibility & identity check across 200 generated instances.
    {
        const std::vector<DurationMode> modes = {
            DurationMode::Pairs, DurationMode::Lower, DurationMode::Upper,
            DurationMode::Mid, DurationMode::Random
        };

        for (std::size_t trial = 0; trial < 200; ++trial) {
            GeneratorConfig cfg;
            cfg.n1 = (trial % 7) + 1;
            cfg.n2 = (trial % 5);
            cfg.n3 = (trial % 3);
            cfg.n4 = (trial % 2);
            cfg.mode = modes[trial % modes.size()];
            cfg.interval_method = static_cast<int>((trial % 4) + 1);
            cfg.min_val = (trial % 2 == 0) ? 20.0 : 2.0;
            cfg.max_val = (trial % 2 == 0) ? 1000.0 : 100.0;
            cfg.beta = 0.3;
            cfg.alpha = 1.5;
            cfg.seed = 10000ULL + trial;

            std::uint64_t seed_a = 0;
            std::uint64_t seed_b = 0;
            const ProblemInstance a = gen::generate_instance(cfg, seed_a);
            const ProblemInstance b = gen::generate_instance(cfg, seed_b);

            expect(seed_a == cfg.seed && seed_b == cfg.seed, "Seed mismatch.");
            expect(io::serialize_problem_text(a) == io::serialize_problem_text(b),
                   "Generator is not deterministic for fixed seed.");

            const ScheduleResult r = algo::evaluate_schedule(a, PermutationMode::Manual);
            const std::int64_t id_cmax = algo::compute_cmax_identity(a, a.perm1, a.perm2);
            expect(r.c_max == id_cmax, "Gantt simulation != identity C_max.");
            expect(r.opt_c_max <= r.c_max, "Bellman-Johnson C_max exceeded manual C_max.");
            expect(r.opt_c_max <= r.anti_c_max, "Bellman-Johnson C_max exceeded anti-optimal C_max.");
        }
    }
}

} // namespace tests

// =============================================================================
// 6. Command-Line Interface
// =============================================================================
namespace cli {

using namespace model;

void print_usage(std::ostream& os, std::string_view prog_name) {
    os << "Jackson 2-Machine Scheduling Core (J2 || C_max)\n"
       << "Usage:\n"
       << "  " << prog_name << " <input.txt> [--perm manual|opt|anti] [--format text|json]\n"
       << "  " << prog_name << " --solve <input.txt> [--perm manual|opt|anti] [--format text|json]\n"
       << "  " << prog_name << " --stdin [--perm manual|opt|anti] [--format text|json]\n"
       << "  " << prog_name << " --generate [options]\n"
       << "  " << prog_name << " --test\n\n"
       << "Generator options:\n"
       << "  --n <int>         Total n = n1 + n2 (auto-computes missing n1 or n2)\n"
       << "  --n1 <int>        Number of Type 1 jobs (M1 -> M2)\n"
       << "  --n2 <int>        Number of Type 2 jobs (M2 -> M1)\n"
       << "  --n3 <int>        Number of Type 3 jobs (M1 only, default: 0)\n"
       << "  --n4 <int>        Number of Type 4 jobs (M2 only, default: 0)\n"
       << "  --mode <str>      pairs | lower | upper | mid | random (default: random)\n"
       << "  --method <1..4>   Interval scheme 1..4 (default: 1)\n"
       << "  --min <num>       Range lower bound (default: 20)\n"
       << "  --max <num>       Range upper bound (default: 1000)\n"
       << "  --beta <num>      Interval parameter beta in [0, 1] (default: 0.3)\n"
       << "  --alpha <num>     Interval parameter alpha > 1 for method 4 (default: 1.5)\n"
       << "  --seed <uint64>   mt19937_64 seed, 0 = random (default: 0)\n"
       << "  --out <path>      Save generated instance to .txt file (else stdout)\n";
}

[[nodiscard]] DurationMode parse_duration_mode(std::string_view s) {
    if (s == "pairs")  return DurationMode::Pairs;
    if (s == "lower")  return DurationMode::Lower;
    if (s == "upper")  return DurationMode::Upper;
    if (s == "mid")    return DurationMode::Mid;
    if (s == "random") return DurationMode::Random;
    throw std::invalid_argument("Unknown duration mode: " + std::string(s));
}

[[nodiscard]] PermutationMode parse_perm_mode(std::string_view s) {
    if (s == "manual") return PermutationMode::Manual;
    if (s == "opt")    return PermutationMode::Optimal;
    if (s == "anti")   return PermutationMode::AntiOptimal;
    throw std::invalid_argument("Unknown permutation mode: " + std::string(s));
}

[[nodiscard]] OutputFormat parse_output_format(std::string_view s) {
    if (s == "text") return OutputFormat::Text;
    if (s == "json") return OutputFormat::Json;
    throw std::invalid_argument("Unknown output format: " + std::string(s));
}

[[nodiscard]] std::size_t parse_size_arg(const std::string& val, std::string_view flag) {
    try {
        const long long parsed = std::stoll(val);
        if (parsed < 0) {
            throw std::invalid_argument("Negative value for " + std::string(flag));
        }
        return static_cast<std::size_t>(parsed);
    } catch (const std::exception&) {
        throw std::invalid_argument("Invalid non-negative integer for " + std::string(flag) + ": " + val);
    }
}

int run(int argc, char* argv[]) {
    if (argc < 2) {
        print_usage(std::cout, argv[0]);
        return EXIT_SUCCESS;
    }

    bool mode_test = false;
    bool mode_generate = false;
    bool mode_stdin = false;
    std::string input_file;
    std::string output_file;
    PermutationMode perm_mode = PermutationMode::Manual;
    OutputFormat out_fmt = OutputFormat::Text;

    GeneratorConfig gen_cfg;
    std::optional<std::size_t> opt_n;
    std::optional<std::size_t> opt_n1;
    std::optional<std::size_t> opt_n2;

    auto require_value = [&](int& i, std::string_view flag) -> std::string {
        if (i + 1 >= argc) {
            throw std::invalid_argument("Missing value for option " + std::string(flag));
        }
        return std::string(argv[++i]);
    };

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "-h" || arg == "--help") {
            print_usage(std::cout, argv[0]);
            return EXIT_SUCCESS;
        } else if (arg == "--test") {
            mode_test = true;
        } else if (arg == "--generate") {
            mode_generate = true;
        } else if (arg == "--stdin") {
            mode_stdin = true;
        } else if (arg == "--solve") {
            input_file = require_value(i, arg);
        } else if (arg == "--perm") {
            perm_mode = parse_perm_mode(require_value(i, arg));
        } else if (arg == "--format") {
            out_fmt = parse_output_format(require_value(i, arg));
        } else if (arg == "--n") {
            opt_n = parse_size_arg(require_value(i, arg), arg);
        } else if (arg == "--n1") {
            opt_n1 = parse_size_arg(require_value(i, arg), arg);
        } else if (arg == "--n2") {
            opt_n2 = parse_size_arg(require_value(i, arg), arg);
        } else if (arg == "--n3") {
            gen_cfg.n3 = parse_size_arg(require_value(i, arg), arg);
        } else if (arg == "--n4") {
            gen_cfg.n4 = parse_size_arg(require_value(i, arg), arg);
        } else if (arg == "--mode") {
            gen_cfg.mode = parse_duration_mode(require_value(i, arg));
        } else if (arg == "--method") {
            gen_cfg.interval_method = std::stoi(require_value(i, arg));
        } else if (arg == "--min") {
            gen_cfg.min_val = std::stod(require_value(i, arg));
        } else if (arg == "--max") {
            gen_cfg.max_val = std::stod(require_value(i, arg));
        } else if (arg == "--beta") {
            gen_cfg.beta = std::stod(require_value(i, arg));
        } else if (arg == "--alpha") {
            gen_cfg.alpha = std::stod(require_value(i, arg));
        } else if (arg == "--seed") {
            gen_cfg.seed = std::stoull(require_value(i, arg));
        } else if (arg == "--out") {
            output_file = require_value(i, arg);
        } else if (!arg.empty() && arg[0] != '-' && input_file.empty()) {
            input_file = arg;
        } else {
            throw std::invalid_argument("Unknown option: " + arg);
        }
    }

    if (mode_test) {
        tests::run_all_tests();
        std::cout << "All verification tests passed.\n";
        return EXIT_SUCCESS;
    }

    if (mode_generate) {
        if (opt_n.has_value()) {
            if (opt_n1.has_value() && opt_n2.has_value()) {
                if (*opt_n1 + *opt_n2 != *opt_n) {
                    throw std::invalid_argument("n1 + n2 must equal n.");
                }
                gen_cfg.n1 = *opt_n1;
                gen_cfg.n2 = *opt_n2;
            } else if (opt_n1.has_value()) {
                if (*opt_n1 > *opt_n) {
                    throw std::invalid_argument("n1 cannot exceed n.");
                }
                gen_cfg.n1 = *opt_n1;
                gen_cfg.n2 = *opt_n - *opt_n1;
            } else if (opt_n2.has_value()) {
                if (*opt_n2 > *opt_n) {
                    throw std::invalid_argument("n2 cannot exceed n.");
                }
                gen_cfg.n2 = *opt_n2;
                gen_cfg.n1 = *opt_n - *opt_n2;
            } else {
                gen_cfg.n1 = (*opt_n + 1) / 2;
                gen_cfg.n2 = *opt_n - gen_cfg.n1;
            }
        } else {
            if (opt_n1.has_value()) gen_cfg.n1 = *opt_n1;
            if (opt_n2.has_value()) gen_cfg.n2 = *opt_n2;
        }

        std::uint64_t actual_seed = 0;
        const ProblemInstance inst = gen::generate_instance(gen_cfg, actual_seed);

        if (!output_file.empty()) {
            io::save_problem_file(output_file, inst);
            std::cout << "Saved instance to " << output_file
                      << " (n1=" << gen_cfg.n1 << ", n2=" << gen_cfg.n2
                      << ", n3=" << gen_cfg.n3 << ", n4=" << gen_cfg.n4
                      << ", seed=" << actual_seed << ")\n";
        } else {
            std::cerr << "Seed: " << actual_seed << '\n';
            std::cout << io::serialize_problem_text(inst);
        }
        return EXIT_SUCCESS;
    }

    ProblemInstance instance;
    if (mode_stdin) {
        std::ostringstream oss;
        oss << std::cin.rdbuf();
        instance = io::parse_problem_text(oss.str());
    } else if (!input_file.empty()) {
        instance = io::load_problem_file(input_file);
    } else {
        throw std::invalid_argument("No input specified. Provide a .txt file, --stdin, --generate, or --test.");
    }

    const ScheduleResult result = algo::evaluate_schedule(instance, perm_mode);
    if (out_fmt == OutputFormat::Json) {
        std::cout << io::format_result_json(result);
    } else {
        std::cout << io::format_result_text(result);
    }
    return EXIT_SUCCESS;
}

} // namespace cli

} // namespace jackson

int main(int argc, char* argv[]) {
    try {
        return jackson::cli::run(argc, argv);
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << '\n';
        return EXIT_FAILURE;
    }
}