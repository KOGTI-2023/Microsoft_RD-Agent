# from rdagent.components.coder.CoSTEER import CoSTEER
# from rdagent.components.coder.CoSTEER.config import CoSTEER_SETTINGS
# from rdagent.components.coder.CoSTEER.evaluators import CoSTEERMultiEvaluator
# from rdagent.core.scenario import Scenario


# class WorkflowCoSTEER(CoSTEER):
#     def __init__(
#         self,
#         scen: Scenario,
#         *args,
#         **kwargs,
#     ) -> None:
#         eva = CoSTEERMultiEvaluator(
#             WorkflowCoSTEEREvaluator(scen=scen), scen=scen
#         )  # Please specify whether you agree running your eva in parallel or not
#         es = WorkflowMultiProcessEvolvingStrategy(scen=scen, settings=CoSTEER_SETTINGS)

#         super().__init__(*args, settings=CoSTEER_SETTINGS, eva=eva, es=es, evolving_version=1, scen=scen, **kwargs)