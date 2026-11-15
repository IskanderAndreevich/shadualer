#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <random>
#include <sstream>
#include <string>
#include <vector>

// Задача решается в положительных целых числах
using Duration = std::int64_t;

// Структура двухэтапной работы (Тип 1: M1 -> M2, Тип 2: M2 -> M1)
struct Job {
    int id = 0;       // Номер требования (1-based)
    Duration op1 = 1; // a_j (Тип 1) или c_j (Тип 2), целое >= 1
    Duration op2 = 1; // b_j (Тип 1) или d_j (Тип 2), целое >= 1
};

// Блок операции для построения диаграммы Гантта
struct OperationBlock {
    std::int32_t machine_id = 0;
    std::int32_t job_type = 0;
    std::int32_t job_number = 0;
    Duration start_time = 0;
    Duration end_time = 0;
};

struct ProblemInput {
    std::vector<Job> type1_jobs;
    std::vector<Job> type2_jobs;
    std::vector<Duration> type3_durations;
    std::vector<Duration> type4_durations;
    std::vector<std::size_t> permutation_type1; // 0-based индексы в type1_jobs
    std::vector<std::size_t> permutation_type2; // 0-based индексы в type2_jobs
};

struct MachineMetrics {
    Duration initial_block_load = 0; // Загрузка стартового блока (T1 или T2)
    Duration total_work_time = 0;    // Суммарное время полезной работы
    Duration completion_time = 0;    // Момент завершения последней операции (C1 или C2)
    Duration internal_idle_time = 0; // Внутренний простой в ожидании операций
    Duration total_idle_time = 0;    // Полный простой прибора до момента C_max
};

struct ScheduleOutput {
    Duration makespan = 0; // Общее время завершения всех работ (C_max)
    MachineMetrics machine1_metrics;
    MachineMetrics machine2_metrics;
    std::vector<OperationBlock> gantt_blocks;
};

// ============================================================================
// 1. ГЕНЕРАТОР ИНТЕРВАЛОВ И ДЛИТЕЛЬНОСТЕЙ (ВИХРЬ МЕРСЕННА std::mt19937_64)
// Генерирует положительные целочисленные длительности на основе 4 методов
// ============================================================================
Duration generate_single_duration(
    std::mt19937_64& rng,
    int method,
    double beta = 0.3,
    double alpha = 1.5)
{
    std::uniform_real_distribution<double> dist(0.0, 1.0);
    auto sample = [&](double min_v, double max_v) {
        return min_v + dist(rng) * (max_v - min_v);
    };

    if (method == 1) {
        // Метод 1: случайная величина U[20, 1000], округленная до целого
        return std::max<Duration>(1, static_cast<Duration>(std::llround(sample(20.0, 1000.0))));
    }

    double lower = sample(20.0, 1000.0);
    double upper = 1000.0;

    if (method == 2) {
        // Метод 2: upper ~ U[lower + beta * (1000 - lower), 1000]
        upper = sample(lower + beta * (1000.0 - lower), 1000.0);
    } else if (method == 3) {
        // Метод 3: upper ~ U[min(lower + 100 * beta, 1000), min(lower + 100, 1000)]
        upper = sample(std::min(lower + 100.0 * beta, 1000.0), std::min(lower + 100.0, 1000.0));
    } else if (method == 4) {
        // Метод 4: upper ~ U[min(lower + beta * lower * (alpha - 1), 1000), min(alpha * lower, 1000)]
        upper = sample(
            std::min(lower + beta * lower * (alpha - 1.0), 1000.0),
            std::min(alpha * lower, 1000.0));
    }
    return std::max<Duration>(1, static_cast<Duration>(std::llround(sample(lower, upper))));
}

Job generate_job(
    std::mt19937_64& rng,
    int id,
    int method,
    double beta = 0.3,
    double alpha = 1.5)
{
    Job job{id, 1, 1};
    job.op1 = generate_single_duration(rng, method, beta, alpha);
    job.op2 = generate_single_duration(rng, method, beta, alpha);
    return job;
}

// Случайная перестановка через Вихрь Мерсенна (алгоритм Фишера — Йетса)
std::vector<std::size_t> build_random_perm(std::size_t n, std::mt19937_64& rng) {
    std::vector<std::size_t> perm(n);
    std::iota(perm.begin(), perm.end(), 0);
    for (std::size_t i = n; i > 1; --i) {
        std::size_t j = static_cast<std::size_t>(rng() % i);
        std::swap(perm[i - 1], perm[j]);
    }
    return perm;
}

// ============================================================================
// 2. ПОИСК ОПТИМУМА (Сортировка по алгоритму Беллмана — Джонсона)
// ============================================================================
std::vector<Job> build_optimal(const std::vector<Job>& jobs) {
    std::vector<Job> group1, group2;
    for (const auto& j : jobs) {
        if (j.op1 <= j.op2) {
            group1.push_back(j);
        } else {
            group2.push_back(j);
        }
    }

    std::sort(group1.begin(), group1.end(), [](const Job& l, const Job& r) {
        if (l.op1 != r.op1) return l.op1 < r.op1;
        if (l.op2 != r.op2) return l.op2 > r.op2;
        return l.id < r.id;
    });

    std::sort(group2.begin(), group2.end(), [](const Job& l, const Job& r) {
        if (l.op2 != r.op2) return l.op2 > r.op2;
        if (l.op1 != r.op1) return l.op1 < r.op1;
        return l.id < r.id;
    });

    group1.insert(group1.end(), group2.begin(), group2.end());
    return group1;
}

// ============================================================================
// 3. ПОИСК АНТИОПТИМУМА (Зеркальный разворот оптимальной перестановки)
// ============================================================================
std::vector<Job> build_anti_optimal(const std::vector<Job>& jobs) {
    std::vector<Job> reversed_opt = build_optimal(jobs);
    std::reverse(reversed_opt.begin(), reversed_opt.end());
    return reversed_opt;
}

std::vector<std::size_t> jobs_to_zero_based_perm(const std::vector<Job>& ordered_jobs) {
    std::vector<std::size_t> perm;
    perm.reserve(ordered_jobs.size());
    for (const auto& j : ordered_jobs) {
        perm.push_back(static_cast<std::size_t>(j.id - 1));
    }
    return perm;
}

// ============================================================================
// 4. РАСЧЕТ ИТОГОВОГО ВРЕМЕНИ И ДИАГРАММЫ ГАНТТА
// ============================================================================
Duration calc_f(const std::vector<Job>& perm) {
    Duration t1 = 0, t2 = 0;
    for (const auto& j : perm) {
        t1 += j.op1;
        t2 = std::max(t1, t2) + j.op2;
    }
    return t2;
}

// Вычисление C_max по доказанному тождеству C_max = max(L1, L2, F1, F2)
Duration calc_cmax(
    const std::vector<Job>& p1,
    const std::vector<Job>& p2,
    const std::vector<Duration>& t3 = {},
    const std::vector<Duration>& t4 = {})
{
    Duration sum_a = 0, sum_b = 0, sum_c = 0, sum_d = 0;
    Duration sum_p = 0, sum_q = 0;
    for (const auto& j : p1) { sum_a += j.op1; sum_b += j.op2; }
    for (const auto& j : p2) { sum_c += j.op1; sum_d += j.op2; }
    for (Duration p : t3) sum_p += p;
    for (Duration q : t4) sum_q += q;

    Duration L1 = sum_a + sum_p + sum_d;
    Duration L2 = sum_c + sum_q + sum_b;
    Duration F1 = calc_f(p1);
    Duration F2 = calc_f(p2);

    return std::max({L1, L2, F1, F2});
}

Duration append_initial_blocks(
    std::int32_t machine_id,
    std::int32_t two_stage_type,
    const std::vector<Job>& two_stage_jobs,
    const std::vector<std::size_t>& permutation,
    std::int32_t single_stage_type,
    const std::vector<Duration>& single_stage_durations,
    std::vector<OperationBlock>& gantt_blocks)
{
    Duration current_time = 0;

    for (std::size_t job_index : permutation) {
        Duration duration = two_stage_jobs[job_index].op1;
        std::int32_t display_number = static_cast<std::int32_t>(job_index + 1);

        OperationBlock block;
        block.machine_id = machine_id;
        block.job_type = two_stage_type;
        block.job_number = display_number;
        block.start_time = current_time;
        block.end_time = current_time + duration;

        gantt_blocks.push_back(block);
        current_time += duration;
    }

    for (std::size_t index = 0; index < single_stage_durations.size(); ++index) {
        Duration duration = single_stage_durations[index];
        std::int32_t display_number = static_cast<std::int32_t>(index + 1);

        OperationBlock block;
        block.machine_id = machine_id;
        block.job_type = single_stage_type;
        block.job_number = display_number;
        block.start_time = current_time;
        block.end_time = current_time + duration;

        gantt_blocks.push_back(block);
        current_time += duration;
    }

    return current_time;
}

MachineMetrics evaluate_conveyor_tail(
    std::int32_t machine_id,
    std::int32_t job_type,
    Duration initial_block_load,
    const std::vector<Job>& jobs,
    const std::vector<std::size_t>& permutation,
    std::vector<OperationBlock>& gantt_blocks)
{
    Duration prev_machine_ready_time = 0;
    Duration curr_machine_free_time = initial_block_load;
    Duration tail_work_duration = 0;

    for (std::size_t job_index : permutation) {
        prev_machine_ready_time += jobs[job_index].op1;

        Duration operation_start = std::max(curr_machine_free_time, prev_machine_ready_time);
        Duration operation_duration = jobs[job_index].op2;
        Duration operation_end = operation_start + operation_duration;
        std::int32_t display_number = static_cast<std::int32_t>(job_index + 1);

        OperationBlock block;
        block.machine_id = machine_id;
        block.job_type = job_type;
        block.job_number = display_number;
        block.start_time = operation_start;
        block.end_time = operation_end;

        gantt_blocks.push_back(block);

        curr_machine_free_time = operation_end;
        tail_work_duration += operation_duration;
    }

    MachineMetrics metrics;
    metrics.initial_block_load = initial_block_load;
    metrics.total_work_time = initial_block_load + tail_work_duration;
    metrics.completion_time = curr_machine_free_time;
    metrics.internal_idle_time = std::max<Duration>(0, metrics.completion_time - metrics.total_work_time);
    metrics.total_idle_time = 0;

    return metrics;
}

ScheduleOutput calculate_schedule(const ProblemInput& input) {
    ScheduleOutput output;

    Duration initial_load_m1 = append_initial_blocks(
        1, 1, input.type1_jobs, input.permutation_type1,
        3, input.type3_durations, output.gantt_blocks);

    Duration initial_load_m2 = append_initial_blocks(
        2, 2, input.type2_jobs, input.permutation_type2,
        4, input.type4_durations, output.gantt_blocks);

    output.machine1_metrics = evaluate_conveyor_tail(
        1, 2, initial_load_m1, input.type2_jobs, input.permutation_type2, output.gantt_blocks);

    output.machine2_metrics = evaluate_conveyor_tail(
        2, 1, initial_load_m2, input.type1_jobs, input.permutation_type1, output.gantt_blocks);

    output.makespan = std::max(
        output.machine1_metrics.completion_time,
        output.machine2_metrics.completion_time);

    output.machine1_metrics.total_idle_time = std::max<Duration>(
        0, output.makespan - output.machine1_metrics.total_work_time);

    output.machine2_metrics.total_idle_time = std::max<Duration>(
        0, output.makespan - output.machine2_metrics.total_work_time);

    return output;
}

// ============================================================================
// ВВОД / ВЫВОД И ВЗАИМОДЕЙСТВИЕ С PYTHON GUI / CLI
// ============================================================================
std::vector<Job> read_two_stage_jobs(std::size_t count) {
    std::vector<Job> jobs(count);
    for (std::size_t index = 0; index < count; ++index) {
        jobs[index].id = static_cast<int>(index + 1);
        std::cin >> jobs[index].op1 >> jobs[index].op2;
    }
    return jobs;
}

std::vector<Duration> read_single_stage_jobs(std::size_t count) {
    std::vector<Duration> durations(count);
    for (std::size_t index = 0; index < count; ++index) {
        std::cin >> durations[index];
    }
    return durations;
}

std::vector<std::size_t> read_zero_based_permutation(std::size_t count) {
    std::vector<std::size_t> permutation(count);
    for (std::size_t index = 0; index < count; ++index) {
        std::size_t one_based_id = 0;
        std::cin >> one_based_id;
        permutation[index] = one_based_id - 1;
    }
    return permutation;
}

std::uint64_t make_random_seed() {
    std::random_device rd;
    std::uint64_t s = (static_cast<std::uint64_t>(rd()) << 32) ^ rd();
    return (s == 0) ? 1ULL : s;
}

void print_full_output(
    const ProblemInput& input,
    const ScheduleOutput& output,
    std::uint64_t seed_used = 0)
{
    auto opt_p1 = build_optimal(input.type1_jobs);
    auto opt_p2 = build_optimal(input.type2_jobs);
    Duration cmax_opt = calc_cmax(opt_p1, opt_p2, input.type3_durations, input.type4_durations);

    auto anti_p1 = build_anti_optimal(input.type1_jobs);
    auto anti_p2 = build_anti_optimal(input.type2_jobs);
    Duration cmax_anti = calc_cmax(anti_p1, anti_p2, input.type3_durations, input.type4_durations);

    std::cout << "C_MAX " << output.makespan << "\n";

    std::cout << "M1 "
              << output.machine1_metrics.initial_block_load << " "
              << output.machine1_metrics.total_work_time << " "
              << output.machine1_metrics.completion_time << " "
              << output.machine1_metrics.internal_idle_time << " "
              << output.machine1_metrics.total_idle_time << "\n";

    std::cout << "M2 "
              << output.machine2_metrics.initial_block_load << " "
              << output.machine2_metrics.total_work_time << " "
              << output.machine2_metrics.completion_time << " "
              << output.machine2_metrics.internal_idle_time << " "
              << output.machine2_metrics.total_idle_time << "\n";

    std::cout << "BLOCKS " << output.gantt_blocks.size() << "\n";
    for (const OperationBlock& block : output.gantt_blocks) {
        std::cout << block.machine_id << " "
                  << block.job_type << " "
                  << block.job_number << " "
                  << block.start_time << " "
                  << block.end_time << "\n";
    }

    // Расширенные данные: оптимум, антиоптимум, перестановки и сгенерированные работы
    std::cout << "OPT_CMAX " << cmax_opt << "\n";
    std::cout << "ANTI_CMAX " << cmax_anti << "\n";

    std::cout << "OPT_PI1";
    for (const auto& j : opt_p1) std::cout << " " << j.id;
    std::cout << "\n";

    std::cout << "OPT_PI2";
    for (const auto& j : opt_p2) std::cout << " " << j.id;
    std::cout << "\n";

    std::cout << "ANTI_PI1";
    for (const auto& j : anti_p1) std::cout << " " << j.id;
    std::cout << "\n";

    std::cout << "ANTI_PI2";
    for (const auto& j : anti_p2) std::cout << " " << j.id;
    std::cout << "\n";

    std::cout << "USED_PI1";
    for (std::size_t idx : input.permutation_type1) std::cout << " " << (idx + 1);
    std::cout << "\n";

    std::cout << "USED_PI2";
    for (std::size_t idx : input.permutation_type2) std::cout << " " << (idx + 1);
    std::cout << "\n";

    std::cout << "SEED " << seed_used << "\n";

    std::cout << "JOBS_T1 " << input.type1_jobs.size();
    for (const auto& j : input.type1_jobs) std::cout << " " << j.op1 << " " << j.op2;
    std::cout << "\n";

    std::cout << "JOBS_T2 " << input.type2_jobs.size();
    for (const auto& j : input.type2_jobs) std::cout << " " << j.op1 << " " << j.op2;
    std::cout << "\n";

    std::cout << "JOBS_T3 " << input.type3_durations.size();
    for (Duration p : input.type3_durations) std::cout << " " << p;
    std::cout << "\n";

    std::cout << "JOBS_T4 " << input.type4_durations.size();
    for (Duration q : input.type4_durations) std::cout << " " << q;
    std::cout << "\n";
}

void run_cli_demo() {
    std::uint64_t seed = make_random_seed();
    std::mt19937_64 rng(seed);

    int n = 6, n1 = 5, n2 = n - n1;
    int method = 1; // 1..4
    double beta = 0.3, alpha = 1.5;

    std::vector<Job> t1(n1), t2(n2);
    for (int i = 0; i < n1; ++i) t1[i] = generate_job(rng, i + 1, method, beta, alpha);
    for (int i = 0; i < n2; ++i) t2[i] = generate_job(rng, i + 1, method, beta, alpha);

    auto opt_p1 = build_optimal(t1);
    auto opt_p2 = build_optimal(t2);
    Duration cmax_opt = calc_cmax(opt_p1, opt_p2);

    auto anti_p1 = build_anti_optimal(t1);
    auto anti_p2 = build_anti_optimal(t2);
    Duration cmax_anti = calc_cmax(anti_p1, anti_p2);

    std::cout << "=== Демонстрация (Вихрь Мерсенна std::mt19937_64, целые числа, seed=" << seed << ") ===\n";
    std::cout << "Всего работ n = " << n << " (n1 = " << n1 << ", n2 = " << n2 << ")\n";
    std::cout << "Оптимальное C_max:     " << cmax_opt << "\n";
    std::cout << "Антиоптимальное C_max: " << cmax_anti << "\n";
}

int main(int argc, char* argv[]) {
    if (argc > 1 && std::string(argv[1]) == "--demo") {
        run_cli_demo();
        return 0;
    }

    std::string first_token;
    if (!(std::cin >> first_token)) {
        run_cli_demo();
        return 0;
    }

    // Режим 1: Генерация через Вихрь Мерсенна (std::mt19937_64) прямо в C++ ядре
    // Формат: GEN <n1> <n2> <n3> <n4> <method> <beta> <alpha> <seed> <mode: RANDOM|OPT|ANTI|IDENTITY>
    if (first_token == "GEN") {
        std::size_t n1 = 0, n2 = 0, n3 = 0, n4 = 0;
        int method = 1;
        double beta = 0.3, alpha = 1.5;
        std::uint64_t seed = 0;
        std::string mode = "RANDOM";

        std::cin >> n1 >> n2 >> n3 >> n4 >> method >> beta >> alpha >> seed;
        if (!(std::cin >> mode)) {
            mode = "RANDOM";
        }

        if (seed == 0) {
            seed = make_random_seed();
        }
        std::mt19937_64 rng(seed);

        ProblemInput input;
        input.type1_jobs.resize(n1);
        input.type2_jobs.resize(n2);
        input.type3_durations.resize(n3);
        input.type4_durations.resize(n4);

        for (std::size_t i = 0; i < n1; ++i) {
            input.type1_jobs[i] = generate_job(rng, static_cast<int>(i + 1), method, beta, alpha);
        }
        for (std::size_t i = 0; i < n2; ++i) {
            input.type2_jobs[i] = generate_job(rng, static_cast<int>(i + 1), method, beta, alpha);
        }
        for (std::size_t i = 0; i < n3; ++i) {
            input.type3_durations[i] = generate_single_duration(rng, method, beta, alpha);
        }
        for (std::size_t i = 0; i < n4; ++i) {
            input.type4_durations[i] = generate_single_duration(rng, method, beta, alpha);
        }

        if (mode == "OPT") {
            input.permutation_type1 = jobs_to_zero_based_perm(build_optimal(input.type1_jobs));
            input.permutation_type2 = jobs_to_zero_based_perm(build_optimal(input.type2_jobs));
        } else if (mode == "ANTI") {
            input.permutation_type1 = jobs_to_zero_based_perm(build_anti_optimal(input.type1_jobs));
            input.permutation_type2 = jobs_to_zero_based_perm(build_anti_optimal(input.type2_jobs));
        } else if (mode == "IDENTITY") {
            input.permutation_type1.resize(n1);
            std::iota(input.permutation_type1.begin(), input.permutation_type1.end(), 0);
            input.permutation_type2.resize(n2);
            std::iota(input.permutation_type2.begin(), input.permutation_type2.end(), 0);
        } else {
            // По умолчанию при генерации вставляется случайная перестановка (RANDOM)
            input.permutation_type1 = build_random_perm(n1, rng);
            input.permutation_type2 = build_random_perm(n2, rng);
        }

        ScheduleOutput output = calculate_schedule(input);
        print_full_output(input, output, seed);
        return 0;
    }

    // Режим 2: Расчет по заданным работам с выбором режима перестановки
    // Формат: EVAL <mode: MANUAL|RANDOM|OPT|ANTI> <n1> <n2> <n3> <n4> ...
    std::string mode = "MANUAL";
    std::size_t count_type1 = 0, count_type2 = 0, count_type3 = 0, count_type4 = 0;

    if (first_token == "EVAL") {
        std::cin >> mode >> count_type1 >> count_type2 >> count_type3 >> count_type4;
    } else {
        count_type1 = static_cast<std::size_t>(std::stoull(first_token));
        std::cin >> count_type2 >> count_type3 >> count_type4;
    }

    ProblemInput input;
    input.type1_jobs = read_two_stage_jobs(count_type1);
    input.permutation_type1 = read_zero_based_permutation(count_type1);

    input.type2_jobs = read_two_stage_jobs(count_type2);
    input.permutation_type2 = read_zero_based_permutation(count_type2);

    input.type3_durations = read_single_stage_jobs(count_type3);
    input.type4_durations = read_single_stage_jobs(count_type4);

    std::uint64_t used_seed = 0;
    if (mode == "OPT") {
        input.permutation_type1 = jobs_to_zero_based_perm(build_optimal(input.type1_jobs));
        input.permutation_type2 = jobs_to_zero_based_perm(build_optimal(input.type2_jobs));
    } else if (mode == "ANTI") {
        input.permutation_type1 = jobs_to_zero_based_perm(build_anti_optimal(input.type1_jobs));
        input.permutation_type2 = jobs_to_zero_based_perm(build_anti_optimal(input.type2_jobs));
    } else if (mode == "RANDOM") {
        used_seed = make_random_seed();
        std::mt19937_64 rng(used_seed);
        input.permutation_type1 = build_random_perm(count_type1, rng);
        input.permutation_type2 = build_random_perm(count_type2, rng);
    }

    ScheduleOutput output = calculate_schedule(input);
    print_full_output(input, output, used_seed);
    return 0;
}