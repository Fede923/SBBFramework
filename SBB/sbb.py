#!/usr/bin/env python
# encoding: utf-8
## vim:ts=4:et:nowrap

import random
import time
import os
import sys
import numpy
import glob
import json
import ntpath
from collections import Counter, defaultdict
from core.program import Program, reset_programs_ids
from core.team import Team, reset_teams_ids
from core.instruction import Instruction
from environments.classification_environment import ClassificationEnvironment
from environments.tictactoe.tictactoe_environment import TictactoeEnvironment
from environments.poker.poker_environment import PokerEnvironment
from environments.sockets.reinforcement_environment_for_sockets import ReinforcementEnvironmentForSockets
from core.selection import Selection
from core.diversity_maintenance import DiversityMaintenance
from utils.helpers import round_value, flatten, round_array
from utils.run_info import RunInfo
from utils.team_reader import initialize_actions_for_second_layer
from config import Config

class SBB:
    """
    The main algorithm of SBB.
    """

    def __init__(self):
        self.current_generation_ = 0
        self.best_scores_per_runs_ = [] # used by tests
        Config.RESTRICTIONS['used_diversities'] = list(Config.USER['advanced_training_parameters']['diversity']['metrics'])
        Config.RESTRICTIONS['genotype_options']['total_registers'] = Config.RESTRICTIONS['genotype_options']['output_registers'] + Config.USER['advanced_training_parameters']['extra_registers']
        self._initialize_seeds()

    def _initialize_seeds(self):
        if isinstance(Config.USER['advanced_training_parameters']['seed'], list):
            self.seeds_per_run_ = Config.USER['advanced_training_parameters']['seed']
        else:
            if not Config.USER['advanced_training_parameters']['seed']:
                Config.USER['advanced_training_parameters']['seed'] = random.randint(0, Config.RESTRICTIONS['max_seed'])
            random.seed(Config.USER['advanced_training_parameters']['seed'])
            self.seeds_per_run_ = []
            for index in range(Config.USER['training_parameters']['runs_total']):
                self.seeds_per_run_.append(random.randint(0, Config.RESTRICTIONS['max_seed']))

    def run(self):
        print "\n### Starting pSBB"

        # initialize the environment and the selection algorithm
        self.environment = self._initialize_environment()
        self.selection = Selection(self.environment)

        overall_info = ""
        overall_info += "\n### CONFIG: "+str(Config.USER)+"\n"
        overall_info +=  "\n### RESTRICTIONS: "+str(Config.RESTRICTIONS)+"\n"
        overall_info += self.environment.metrics()
        overall_info += "\nSeeds per run: "+str(self.seeds_per_run_)
        overall_info += "\nDiversities: "+str(Config.RESTRICTIONS['used_diversities'])
        print overall_info

        run_infos = []
        for run_id in range(Config.USER['training_parameters']['runs_total']):
            start_time = time.time()
            run_info = RunInfo(run_id+1, self.environment, self.seeds_per_run_[run_id])
            print "\nStarting run: "+str(run_info.run_id)

            self._set_seed(run_info.seed)

            # initialize actions
            if Config.USER['advanced_training_parameters']['second_layer']['enabled']:
                path = str(Config.USER['advanced_training_parameters']['second_layer']['path']).replace("[run_id]", str(run_info.run_id))
                if not os.path.exists(path):
                    raise ValueError("Path for second layer actions doesn't exist: "+str(path))
                initialize_actions_for_second_layer(path, self.environment)
                total_team_actions = len(Config.RESTRICTIONS['second_layer']['action_mapping'])
                Config.RESTRICTIONS['total_actions'] = total_team_actions

            # randomly initialize populations
            self.current_generation_ = 0
            teams_population, programs_population = self._initialize_populations()
            
            self.environment.reset()

            while not self._stop_criterion():
                self.current_generation_ += 1
                
                if self.current_generation_ == 1 or self.current_generation_ % Config.USER['training_parameters']['validate_after_each_generation'] == 0:
                    validation = True
                else:
                    validation = False

                # selection
                teams_population, programs_population, pareto_front = self.selection.run(self.current_generation_, teams_population, programs_population, validation)

                # final pruning
                if self._stop_criterion():
                    older_teams = [team for team in teams_population if team.generation != self.current_generation_]
                    for team in older_teams:
                        team.prune_total()

                # validation
                if not validation:
                    print ".",
                    sys.stdout.flush()
                    self._store_per_generation_metrics(run_info, teams_population)
                else:
                    best_team = self.environment.validate(self.current_generation_, teams_population)
                    self._store_per_generation_metrics(run_info, teams_population)
                    self._print_and_store_per_validation_metrics(run_info, best_team, teams_population, programs_population)

            # to ensure validation metrics exist for all teams in the hall of fame
            if Config.USER['task'] == 'reinforcement' and Config.USER['reinforcement_parameters']['hall_of_fame']['enabled']:
                print "Validating hall of fame..."
                self.environment.validate(self.current_generation_, self.environment.hall_of_fame())

            self._store_per_run_metrics(run_info, best_team, teams_population, pareto_front)
            run_info.elapsed_time_ = round_value((time.time() - start_time)/60.0)
            print("\nFinished run "+str(run_info.run_id)+", elapsed time: "+str(run_info.elapsed_time_)+" mins")
            run_infos.append(run_info)
            sys.stdout.flush()
        
        # finalize execution (get final metrics, print to output, print to file)
        overall_info += self._generate_overall_metrics_output(run_infos)
        print overall_info
        sys.stdout.flush()

        if Config.RESTRICTIONS['write_output_files']:
            self.filepath_ = self._create_folder()
            self._write_output_files(run_infos, overall_info)
    
    def _initialize_environment(self):
        environment = None
        if Config.USER['task'] == 'classification':
            environment = ClassificationEnvironment()
        if Config.USER['task'] == 'reinforcement':
            if Config.USER['reinforcement_parameters']['environment'] == 'tictactoe':
                environment = TictactoeEnvironment()
            if Config.USER['reinforcement_parameters']['environment'] == 'poker':
                environment = PokerEnvironment()
            if Config.USER['reinforcement_parameters']['environment'] == 'sockets':
                environment = ReinforcementEnvironmentForSockets()
        if environment is None:
            raise ValueError("No environment exists for "+str(Config.USER['task']))
        return environment

    def _set_seed(self, seed):
        random.seed(seed)
        numpy.random.seed(seed)

    def _create_folder(self):
        if not os.path.exists("outputs/"):
            os.makedirs("outputs/")
        localtime = time.localtime()
        hours = "%02d%02d%02d" % (localtime.tm_hour,localtime.tm_min,localtime.tm_sec,)
        pretty_localtime = str(localtime.tm_year)+"-"+str(localtime.tm_mon)+"-"+str(localtime.tm_mday)+"-"+hours
        if Config.USER['task'] == 'classification':
            filename = Config.USER['classification_parameters']['dataset']
        else:
            filename = Config.USER['reinforcement_parameters']['environment']
        filepath = "outputs/"+str(filename)+"_"+pretty_localtime+"/"
        os.makedirs(filepath)
        return filepath

    def _initialize_populations(self):
        """
        Initialize a population of teams with ['team_size']['min'] unique random programs with distinct actions.
        Then randomly add already created programs to the teams.
        """
        if Config.USER['training_parameters']['team_size']['min'] > Config.RESTRICTIONS['total_actions']:
            raise ValueError("The team minimum size is lower than the total number of actions, it is not possible to initialize a distinct set of actions per team!")
        
        # randomly initialize teams with the minimum size
        reset_teams_ids()
        reset_programs_ids()
        teams_population = []
        programs_population = []
        for t in range(Config.USER['training_parameters']['populations']['teams']):
            available_actions = range(Config.RESTRICTIONS['total_actions'])
            programs = []
            for index in range(Config.USER['training_parameters']['team_size']['min']):
                program = self._initialize_random_program(available_actions)
                available_actions.remove(program.action)
                programs.append(program)
            team = Team(self.current_generation_, programs, self.environment)
            teams_population.append(team)
            programs_population += programs

        return teams_population, programs_population

    def _initialize_random_program(self, available_actions):
        instructions = []
        total_instructions = random.randrange(Config.USER['training_parameters']['program_size']['min'], Config.USER['training_parameters']['program_size']['max']+1)
        for i in range(total_instructions):
            instructions.append(Instruction())
        action = random.choice(available_actions)
        program = Program(self.current_generation_, instructions, action)
        return program

    def _stop_criterion(self):
        if self.current_generation_ == Config.USER['training_parameters']['generations_total']:
            return True
        return False

    def _print_and_store_per_validation_metrics(self, run_info, best_team, teams_population, programs_population):
        print "\n\n>>>>> Generation: "+str(self.current_generation_)+", run: "+str(run_info.run_id)
        run_info.train_score_per_validation_.append(best_team.fitness_)
        run_info.test_score_per_validation_.append(best_team.score_testset_)
        if Config.USER['task'] == 'classification':
            run_info.recall_per_validation_.append(best_team.extra_metrics_['recall_per_action'])
        print("\n### Best Team Metrics: "+best_team.metrics()+"\n")

        print "\n### Global Metrics:"

        older_teams = [team for team in teams_population if team.generation != self.current_generation_]

        fitness_score_mean = round_value(numpy.mean([team.fitness_ for team in older_teams]))
        fitness_score_std = round_value(numpy.std([team.fitness_ for team in older_teams]))
        
        if Config.USER['task'] == 'reinforcement':
            validation_score_mean = round_value(numpy.mean([team.extra_metrics_['validation_score'] for team in older_teams]))
            opponent_means = {}
            for key in older_teams[0].extra_metrics_['validation_opponents']:
                opponent_means[key] = round_value(numpy.mean([t.extra_metrics_['validation_opponents'][key] for t in older_teams]))    
            if 'hall_of_fame' in best_team.extra_metrics_['champion_opponents']:
                opponent_means['hall_of_fame(champion)'] = best_team.extra_metrics_['champion_opponents']['hall_of_fame']
            run_info.global_mean_validation_score_per_validation_.append(validation_score_mean)
            run_info.global_max_validation_score_per_validation_.append(round_value(max([team.extra_metrics_['validation_score'] for team in older_teams])))
            run_info.global_opponent_results_per_validation_.append(opponent_means)               
            print "\nglobal validation score (mean): "+str(validation_score_mean)+"\n"
            run_info.final_teams_validations_ = [team.extra_metrics_['validation_score'] for team in older_teams]
        if Config.USER['task'] == 'classification':
            validation_score_mean = round_value(numpy.mean([team.score_testset_ for team in older_teams]))
            run_info.global_mean_validation_score_per_validation_.append(validation_score_mean)
            print "\nglobal validation score (mean): "+str(validation_score_mean)+"\n"

        for key in best_team.diversity_:
            run_info.global_diversity_per_validation_[key].append(run_info.global_diversity_per_generation_[key][-1])
            print str(key)+": "+str(best_team.diversity_[key])+" (global: "+str(run_info.global_diversity_per_generation_[key][-1])+")"

        print "\nfitness, mean (global): "+str(fitness_score_mean)
        print "\nfitness, std (global): "+str(fitness_score_std)

        actions_distribution = Counter([p.action for p in programs_population])
        print "\nactions distribution: "+str(actions_distribution)
        actions_distribution_array = []
        for action in range(Config.RESTRICTIONS['total_actions']):
            if action in actions_distribution:
                actions_distribution_array.append(actions_distribution[action])
            else:
                actions_distribution_array.append(0)
        run_info.actions_distribution_per_validation_.append(actions_distribution_array)

        inputs_distribution_per_instruction = Counter()
        inputs_distribution_per_team = Counter()
        for team in older_teams:
            inputs_distribution_per_instruction.update(team.inputs_distribution())
            inputs_distribution_per_team.update(list(team.inputs_distribution()))
        inputs_distribution_per_instruction_array = []
        inputs_distribution_per_team_array = []
        for value in range(Config.RESTRICTIONS['total_inputs']):
            if value in inputs_distribution_per_instruction:
                inputs_distribution_per_instruction_array.append(inputs_distribution_per_instruction[value])
            else:
                inputs_distribution_per_instruction_array.append(0)
            if value in inputs_distribution_per_team:
                inputs_distribution_per_team_array.append(inputs_distribution_per_team[value])
            else:
                inputs_distribution_per_team_array.append(0)
        print "inputs distribution (global, per program): "+str(inputs_distribution_per_instruction_array)
        print "inputs distribution (global, per team): "+str(inputs_distribution_per_team_array)
        run_info.inputs_distribution_per_instruction_per_validation_.append(inputs_distribution_per_instruction_array)
        run_info.inputs_distribution_per_team_per_validation_.append(inputs_distribution_per_team_array)

        print
        print "Global Fitness (last 10 gen.): "+str(run_info.global_mean_fitness_per_generation_[-10:])
               
        if len(Config.RESTRICTIONS['used_diversities']) > 0:
            print "Global Diversity (last 10 gen.):"
            for diversity in Config.RESTRICTIONS['used_diversities']:
                print "- "+str(diversity)+": "+str(run_info.global_diversity_per_generation_[diversity][-10:])
        if len(Config.RESTRICTIONS['used_diversities']) > 1:
            print "Diversity Type (last 10 gen.): "+str(run_info.novelty_type_per_generation_[-10:])

        if Config.USER['task'] == 'reinforcement' and Config.USER['reinforcement_parameters']['environment'] == 'poker':
            self.environment.calculate_poker_metrics_per_validation(run_info)
            print
            print "Point Population Distribution per Validation (last gen.):"
            for attribute in run_info.point_population_distribution_per_validation_:
                temp = []
                for key in run_info.point_population_distribution_per_validation_[attribute]:
                    temp.append(str(key)+": "+str(run_info.point_population_distribution_per_validation_[attribute][key][-1]))
                print "- "+str(attribute)+" = "+", ".join(temp)
            print
            print "Validation Population Distribution per Validation: "+str(run_info.validation_population_distribution_per_validation_)
            print "Global Point Results per Validation: "
            for attribute in run_info.global_result_per_validation_:
                temp = []
                for key in run_info.global_result_per_validation_[attribute]:
                    temp.append(str(key)+": "+str(run_info.global_result_per_validation_[attribute][key][-1]))
                print "- "+str(attribute)+" = "+", ".join(temp)
            print
            print "Champion Population Distribution per Validation: "+str(run_info.champion_population_distribution_per_validation_)
            
        if Config.USER['task'] == 'reinforcement' and Config.USER['reinforcement_parameters']['hall_of_fame']['enabled']:
            run_info.hall_of_fame_per_validation_.append([p.__repr__() for p in self.environment.hall_of_fame()])
            print "\nHall of Fame: "+str(run_info.hall_of_fame_per_validation_[-1])

        avg_team_size = round_value(numpy.mean([len(team.programs) for team in older_teams]))
        avg_program_with_intros_size = round_value(numpy.mean(flatten([[len(program.instructions) for program in team.programs] for team in older_teams])))
        avg_program_without_intros_size = round_value(numpy.mean(flatten([[len(program.instructions_without_introns_) for program in team.programs] for team in older_teams])))
        run_info.mean_team_size_per_validation_.append(avg_team_size)
        run_info.mean_program_size_with_introns_per_validation_.append(avg_program_with_intros_size)
        run_info.mean_program_size_without_introns_per_validation_.append(avg_program_without_intros_size)
        print "\nMean Team Sizes: "+str(run_info.mean_team_size_per_validation_[-10:])
        print "Mean Program Sizes (with introns): "+str(run_info.mean_program_size_with_introns_per_validation_[-10:])
        print "Mean Program Sizes (without introns): "+str(run_info.mean_program_size_without_introns_per_validation_[-10:])

        print "\n<<<<< Generation: "+str(self.current_generation_)+", run: "+str(run_info.run_id)

    def _store_per_generation_metrics(self, run_info, teams_population):
        older_teams = [team for team in teams_population if team.generation != self.current_generation_]
        mean_fitness = round_value(numpy.mean([team.fitness_ for team in older_teams]), 3)
        run_info.global_mean_fitness_per_generation_.append(mean_fitness)
        run_info.global_max_fitness_per_generation_.append(round_value(max([team.fitness_ for team in older_teams])))
        for diversity in Config.RESTRICTIONS['used_diversities']:
            run_info.global_diversity_per_generation_[diversity].append(round_value(numpy.mean([t.diversity_[diversity] for t in older_teams]), 3))
        if len(Config.RESTRICTIONS['used_diversities']) > 1 and self.selection.previous_diversity_:
            run_info.global_fitness_per_diversity_per_generation_[self.selection.previous_diversity_].append(mean_fitness)
            run_info.novelty_type_per_generation_.append(Config.RESTRICTIONS['used_diversities'].index(self.selection.previous_diversity_))
        if Config.USER['task'] == 'reinforcement':
            opponents = older_teams[0].extra_metrics_['training_opponents'].keys()
            for opponent in opponents:
                mean_fitness_per_opponent = round_value(numpy.mean([team.extra_metrics_['training_opponents'][opponent] for team in older_teams]), 3)
                run_info.global_fitness_per_opponent_per_generation_[opponent].append(mean_fitness_per_opponent)

    def _store_per_run_metrics(self, run_info, best_team, teams_population, pareto_front):
        run_info.best_team_ = best_team
        for team in teams_population:
            if team.generation != self.current_generation_:
                run_info.teams_in_last_generation_.append(team)
        run_info.pareto_front_in_last_generation_ = pareto_front
        run_info.hall_of_fame_in_last_generation_ = self.environment.hall_of_fame()
        if Config.USER['task'] == 'reinforcement':
            self.environment.calculate_final_validation_metrics(run_info, teams_population, self.current_generation_)

    def _generate_overall_metrics_output(self, run_infos):       
        msg = "\n\n\n#################### OVERALL RESULTS ####################"

        score_means, score_stds = self._process_scores([run.global_mean_validation_score_per_validation_ for run in run_infos])
        msg += "\n\nGlobal Mean Validation Score per Validation:"
        msg += "\nmean: "+str(score_means)
        msg += "\nstd. deviation: "+str(score_stds)

        if Config.USER['task'] == 'reinforcement':
            score_means, score_stds = self._process_scores([run.global_max_validation_score_per_validation_ for run in run_infos])
            msg += "\n\nGlobal Max. Validation Score per Validation:"
            msg += "\nmean: "+str(score_means)
            msg += "\nstd. deviation: "+str(score_stds)

        msg += "\n\nGlobal Diversities per Validation:"
        for key in Config.RESTRICTIONS['used_diversities']:
            score_means, score_stds = self._process_scores([run.global_diversity_per_validation_[key] for run in run_infos])
            msg += "\n- "+str(key)+":"
            msg += "\n- mean: "+str(score_means)
            msg += "\n- std. deviation: "+str(score_stds)

        if Config.USER['task'] == 'reinforcement':
            msg += "\n\nGlobal Fitness per Opponent per Training:"
            for key in self.environment.opponent_names_for_training_:
                score_means, score_stds = self._process_scores([run.global_fitness_per_opponent_per_generation_[key] for run in run_infos])
                msg += "\n- "+str(key)+":"
                msg += "\n- mean: "+str(round_array(score_means, 2))
                msg += "\n- std. deviation: "+str(round_array(score_stds, 2))
            for run_id, run in enumerate(run_infos):
                valid_names = [t.__repr__() for t in run.hall_of_fame_in_last_generation_]
                for key in run.global_fitness_per_opponent_per_generation_.keys():
                    if key in valid_names:
                        msg += "\n- run "+str(run_id+1)+", "+str(key)+": "+str(run.global_fitness_per_opponent_per_generation_[key])

        score_means, score_stds = self._process_scores([run.test_score_per_validation_ for run in run_infos])
        msg += "\n\nBest Team Validation Score per Validation (champion):"
        msg += "\nmean: "+str(score_means)
        msg += "\nstd. deviation: "+str(score_stds)

        score_means, score_stds = self._process_scores([run.global_mean_fitness_per_generation_ for run in run_infos])
        msg += "\n\nGlobal Mean Fitness Score per Training:"
        msg += "\nmean: "+str(round_array(score_means, 3))
        msg += "\nstd. deviation: "+str(round_array(score_stds, 3))

        score_means, score_stds = self._process_scores([run.global_max_fitness_per_generation_ for run in run_infos])
        msg += "\n\nGlobal Max. Fitness Score per Training:"
        msg += "\nmean: "+str(round_array(score_means, 3))
        msg += "\nstd. deviation: "+str(round_array(score_stds, 3))

        if Config.USER['task'] == 'reinforcement':
            msg += "\n\nFinal Teams Validations: "+str(flatten([round_array(run.final_teams_validations_, 3) for run in run_infos]))
        
        if not Config.USER['advanced_training_parameters']['second_layer']['enabled']:
            score_means, score_stds = self._process_scores([run.actions_distribution_per_validation_[-1] for run in run_infos])
            msg += "\n\nDistribution of Actions per Validation (last gen.):"
            msg += "\nmean: "+str(round_array(score_means, 2))
            msg += "\nstd. deviation: "+str(round_array(score_stds, 2))
        score_means, score_stds = self._process_scores([run.inputs_distribution_per_instruction_per_validation_[-1] for run in run_infos])
        msg += "\nDistribution of Inputs per Validation (per program) (last gen.):"
        msg += "\nmean: "+str(round_array(score_means, 2))
        msg += "\nstd. deviation: "+str(round_array(score_stds, 2))
        score_means, score_stds = self._process_scores([run.inputs_distribution_per_team_per_validation_[-1] for run in run_infos])
        msg += "\nDistribution of Inputs per Validation (per team) (last gen.):"
        msg += "\nmean: "+str(round_array(score_means, 2))
        msg += "\nstd. deviation: "+str(round_array(score_stds, 2))

        msg += "\n\nMean Team Sizes (last gen.): "+str(numpy.mean([run.mean_team_size_per_validation_[-1] for run in run_infos]))
        msg += "\nMean Program Sizes (with introns) (last gen.): "+str(numpy.mean([run.mean_program_size_with_introns_per_validation_[-1] for run in run_infos]))
        msg += "\nMean Program Sizes (without introns) (last gen.): "+str(numpy.mean([run.mean_program_size_without_introns_per_validation_[-1] for run in run_infos]))
        
        msg += "\n"
        if Config.USER['task'] == 'reinforcement':
            msg += self._generate_overall_metrics_output_for_acc_curves(run_infos)

        msg += "\n\n######"

        final_scores = [run.global_mean_validation_score_per_validation_[-1] for run in run_infos]
        msg += "\n\nGlobal Mean Validation Score per Validation per Run: "+str(final_scores)
        msg += "\nmean: "+str(round_value(numpy.mean(final_scores)))
        msg += "\nstd. deviation: "+str(round_value(numpy.std(final_scores)))
        best_run = run_infos[final_scores.index(max(final_scores))]
        msg += "\nbest run: "+str(best_run.run_id)

        score_per_run = []
        for run in run_infos:
            score_per_run.append(round_value(run.best_team_.score_testset_))
        self.best_scores_per_runs_ = score_per_run
        msg += "\n\nBest Team Validation Score per Validation per Run (champion): "+str(score_per_run)
        msg += "\nmean: "+str(round_value(numpy.mean(score_per_run)))
        msg += "\nstd. deviation: "+str(round_value(numpy.std(score_per_run)))
        scores = [run.best_team_.score_testset_ for run in run_infos]
        best_run = run_infos[scores.index(max(scores))]
        msg += "\nbest run: "+str(best_run.run_id)

        msg += "\n\n######"

        elapseds_per_run = [run.elapsed_time_ for run in run_infos]
        msg += "\n\nFinished execution, total elapsed time: "+str(round_value(sum(elapseds_per_run)))+" mins "
        msg += "(mean: "+str(round_value(numpy.mean(elapseds_per_run)))+", std: "+str(round_value(numpy.std(elapseds_per_run)))+")"
        return msg

    def _process_scores(self, score_per_generation_per_run):
        score_means = []
        score_stds = []
        for index in range(len(score_per_generation_per_run[0])):
            column = [row[index] for row in score_per_generation_per_run]
            score_means.append(round_value(numpy.mean(column)))
            score_stds.append(round_value(numpy.std(column)))
        return score_means, score_stds

    def _generate_overall_metrics_output_for_acc_curves(self, run_infos):
        msg = ""
        metric = "score"
        msg += "\nOverall Accumulative Results ("+str(metric)+"):"
        score_means, score_stds = self._process_scores([run.individual_performance_in_last_generation_[metric] for run in run_infos])
        msg += "\n- Individual Team Performance:"
        msg += "\nmean: "+str(round_array(score_means, 3))
        msg += "\nstd. deviation: "+str(round_array(score_stds, 3))
        score_means, score_stds = self._process_scores([run.accumulative_performance_in_last_generation_[metric] for run in run_infos])
        msg += "\n- Accumulative Team Performance:"
        msg += "\nmean: "+str(round_array(score_means, 3))
        msg += "\nstd. deviation: "+str(round_array(score_stds, 3))
        msg += "\n\nAccumulative Results per Run ("+str(metric)+"):"
        msg += "\nindividual_values = ["
        for run in run_infos:
            msg += "\n"+str(run.individual_performance_in_last_generation_[metric])+","
        msg += "\n]"
        msg += "\nacc_values = ["
        for run in run_infos:
            msg += "\n"+str(run.accumulative_performance_in_last_generation_[metric])+","
        msg += "\n]"
        return msg

    def _write_output_files(self, run_infos, overall_info):
        with open(self.filepath_+"metrics_overall.txt", "w") as text_file:
            text_file.write(overall_info)
        for run in run_infos:
            path = self.filepath_+"run"+str(run.run_id)+"/"
            os.makedirs(path)
            with open(path+"metrics.txt", "w") as text_file:
                text_file.write(str(run))
            with open(path+"best_team.txt", "w") as text_file:
                text_file.write(str(run.best_team_))
            with open(path+"best_team.json", "w") as text_file:
                text_file.write(run.best_team_.json())
            self._save_teams(run.teams_in_last_generation_, path+"last_generation_teams/")
            self._save_teams(run.pareto_front_in_last_generation_, path+"last_pareto_front/")
            self._save_teams(run.hall_of_fame_in_last_generation_, path+"last_hall_of_fame/")
            os.makedirs(path+"second_layer_files/")
            for key in run.second_layer_files_.keys():
                self._save_teams_in_actions_file(run.second_layer_files_[key], path+"second_layer_files/"+key+"_")
        print "\n### Files saved at "+self.filepath_+"\n"

    def _save_teams(self, teams, path):
        if len(teams) > 0:
            os.makedirs(path)
            json_path = path+"json/"
            os.makedirs(json_path)
            for team in teams:
                with open(path+team.__repr__()+".txt", "w") as text_file:
                    text_file.write(str(team))
                with open(json_path+team.__repr__()+".json", "w") as text_file:
                    text_file.write(team.json())

    def _save_teams_in_actions_file(self, teams, path):
        if len(teams) > 0:
            actions = {}
            for index, team in enumerate(teams):
                actions[index] = team.dict()
            with open(path+"actions.json", "w") as text_file:
                text_file.write(json.dumps(actions))