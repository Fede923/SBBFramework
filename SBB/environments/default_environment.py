import abc
from ..config import Config

def reset_points_ids():
    global next_point_id
    next_point_id = 0

def get_point_id():
    global next_point_id
    next_point_id += 1
    return next_point_id

class DefaultPoint(object):
    """
    Encapsulates a value from the environment as a point.
    """
    __metaclass__  = abc.ABCMeta

    def __init__(self):
        self.point_id_ = get_point_id()
        self.age_ = 0

    def __repr__(self): 
        return "("+str(self.point_id_)+")"

    def __str__(self): 
        return "("+str(self.point_id_)+")"

class DefaultEnvironment(object):
    """
    Abstract class for environments. All environments must implement these 
    methods to be able to work with SBB.
    """
    __metaclass__  = abc.ABCMeta

    @abc.abstractmethod
    def __init__(self):
        """
        Initialize the environment variables.
        """

    @abc.abstractmethod
    def point_population(self):
         """
         Return the point_population. If the Hall of Fame is being used, it will return 
         the point_population plus the hall_of_fame, since both were used during training.
         """

    @abc.abstractmethod
    def reset(self):
         """
         Method that is called at the beginning of each run by SBB, to reset the 
         variables that will be used by the generations.
         """

    @abc.abstractmethod
    def setup(self, teams_population):
         """
         Method that is called at the beginning of each generation by SBB, to set the 
         variables that will be used by the generationand remove the ones that are no 
         longer being used.
         """

    @abc.abstractmethod
    def evaluate_point_population(self, teams_population):
        """
        Evaluate the fitness of the point population, to define which points will be removed 
        or added in the next generation, when setup_point_population() is executed.
        """

    @abc.abstractmethod
    def evaluate_teams_population_for_training(self, teams_population):
        """
        Evaluate all the teams using the evaluate_team() method, and sets metrics. Used only 
        for training.
        """

    @abc.abstractmethod
    def evaluate_team(self, team, mode):
        """
        Evaluate the team using the environment inputs. May be executed in the training
        or the test mode.
        This method must set the attribute results_per_points of the team, if you intend to 
        use pareto.
        """

    @abc.abstractmethod
    def validate(self, current_generation, teams_population):
        """
        Return the best team for the teams_population using the validation set. It must 
        also set the team.score_testset_ and, if necessary, team.extra_metrics_
        """

    @abc.abstractmethod
    def metrics_for_team(self, team):
        """
        Generate a string with the metrics for the team specific of the environment.
        """

    @abc.abstractmethod
    def initialize_attributes_for_run_info(self, run_info):
        """
        Initialize the attributes in run_info, that will be the output at the end of the run.
        """

    @abc.abstractmethod
    def generate_output_for_attributes_for_run_info(self, run_info):
        """
        Generate an output of the attributes in run_info, to append it to the other results of the run.
        """

    @abc.abstractmethod
    def metrics(self):
        """
        Generate a string with the metrics for the environment. It is printed at the 
        start and at the end of the execution, and it is also saved in the output file.
        """

    def hall_of_fame(self):
        return []