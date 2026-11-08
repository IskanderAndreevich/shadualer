#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <vector>

using Duration = double;

struct TwoStageJob {
    Duration first_stage_duration = 0.0;
    Duration second_stage_duration = 0.0;
};

struct OperationBlock {
    std::int32_t machine_id = 0;
    std::int32_t job_type = 0;
    std::int32_t job_number = 0;
    Duration start_time = 0.0;
    Duration end_time = 0.0;
};

struct ProblemInput {
    std::vector<TwoStageJob> type1_jobs;
    std::vector<TwoStageJob> type2_jobs;
    std::vector<Duration> type3_durations;
    std::vector<Duration> type4_durations;
    std::vector<std::size_t> permutation_type1;
    std::vector<std::size_t> permutation_type2;
};

struct MachineMetrics {
    Duration initial_block_load = 0.0; // Загрузка стартового блока (T1 или T2)
    Duration total_work_time = 0.0;    // Суммарное время полезной работы
    Duration completion_time = 0.0;    // Момент завершения последней операции (C1 или C2)
    Duration internal_idle_time = 0.0; // Внутренний простой в ожидании операций
    Duration total_idle_time = 0.0;    // Полный простой прибора до момента C_max
};

struct ScheduleOutput {
    Duration makespan = 0.0; // Общее время завершения всех работ (C_max)
    MachineMetrics machine1_metrics;
    MachineMetrics machine2_metrics;
    std::vector<OperationBlock> gantt_blocks;
};


Duration append_initial_blocks(
    std::int32_t machine_id,
    std::int32_t two_stage_type,
    const std::vector<TwoStageJob>& two_stage_jobs,
    const std::vector<std::size_t>& permutation,
    std::int32_t single_stage_type,
    const std::vector<Duration>& single_stage_durations,
    std::vector<OperationBlock>& gantt_blocks)
{
    Duration current_time = 0.0;

    for (std::size_t job_index : permutation) {
        Duration duration = two_stage_jobs[job_index].first_stage_duration;
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
    const std::vector<TwoStageJob>& jobs,
    const std::vector<std::size_t>& permutation,
    std::vector<OperationBlock>& gantt_blocks)
{
    Duration prev_machine_ready_time = 0.0;
    Duration curr_machine_free_time = initial_block_load;
    Duration tail_work_duration = 0.0;

    for (std::size_t job_index : permutation) {
        prev_machine_ready_time += jobs[job_index].first_stage_duration;

        Duration operation_start = std::max(curr_machine_free_time, prev_machine_ready_time);
        Duration operation_duration = jobs[job_index].second_stage_duration;
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
    metrics.internal_idle_time = metrics.completion_time - metrics.total_work_time;
    metrics.total_idle_time = 0.0;

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

    output.machine1_metrics.total_idle_time =
        output.makespan - output.machine1_metrics.total_work_time;

    output.machine2_metrics.total_idle_time =
        output.makespan - output.machine2_metrics.total_work_time;

    return output;
}


std::vector<TwoStageJob> read_two_stage_jobs(std::size_t count) {
    std::vector<TwoStageJob> jobs(count);
    for (std::size_t index = 0; index < count; ++index) {
        std::cin >> jobs[index].first_stage_duration;
        std::cin >> jobs[index].second_stage_duration;
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

ProblemInput read_problem_input() {
    std::size_t count_type1 = 0;
    std::size_t count_type2 = 0;
    std::size_t count_type3 = 0;
    std::size_t count_type4 = 0;

    if (!(std::cin >> count_type1 >> count_type2 >> count_type3 >> count_type4)) {
        return ProblemInput{};
    }

    ProblemInput input;
    input.type1_jobs = read_two_stage_jobs(count_type1);
    input.permutation_type1 = read_zero_based_permutation(count_type1);

    input.type2_jobs = read_two_stage_jobs(count_type2);
    input.permutation_type2 = read_zero_based_permutation(count_type2);

    input.type3_durations = read_single_stage_jobs(count_type3);
    input.type4_durations = read_single_stage_jobs(count_type4);

    return input;
}

void print_schedule_output(const ScheduleOutput& output) {
    std::cout << std::fixed << std::setprecision(2);
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
}

int main() {
    ProblemInput input = read_problem_input();
    ScheduleOutput output = calculate_schedule(input);
    print_schedule_output(output);
    return 0;
}