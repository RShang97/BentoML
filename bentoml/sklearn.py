import os
import typing as t

import numpy as np
from simple_di import Provide, inject

from ._internal.configuration.containers import BentoMLContainer
from ._internal.models import MODEL_EXT, SAVE_NAMESPACE
from ._internal.service.runner import Runner
from .exceptions import BentoMLException, MissingDependencyException

_MT = t.TypeVar("_MT")

try:
    import sklearn.externals.joblib as joblib
    from joblib import parallel_backend

except ImportError:
    raise MissingDependencyException(
        "sklearn is required in order to use bentoml.sklearn. Do `pip install sklearn`"
    )

if t.TYPE_CHECKING:
    import pandas as pd

    from ._internal.models.store import ModelStore


def _get_model_info(tag: str, model_store: "ModelStore"):
    model_info = model_store.get(tag)
    if model_info.module != __name__:
        raise BentoMLException(
            f"Model {tag} was saved with module {model_info.module}, failed loading "
            f"with {__name__}."
        )
    model_file = os.path.join(model_info.path, f"{SAVE_NAMESPACE}.pkl")

    return model_info, model_file


@inject
def load(
    tag: str,
    model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
) -> _MT:
    """
    Load a model from BentoML local modelstore with given name.

    Args:
        tag (`str`):
            Tag of a saved model in BentoML local modelstore.

    Returns:
        an instance of sklearn model from BentoML modelstore.

    Examples:
        import bentoml.sklearn
        sklearn = bentoml.sklearn.load(
            'my_model:20201012_DE43A2')

    """
    _, model_file = _get_model_info(tag, model_store)

    return joblib.load(filename=model_file)


@inject
def save(
    name: str,
    model: _MT,
    *,
    metadata: t.Optional[t.Dict[str, t.Any]] = None,
    model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
) -> str:
    """
    Save a model instance to BentoML modelstore.

    Args:
        name (`str`):
            Name for given model instance. This should pass Python identifier check.
        model (``):
            Instance of model to be saved
        sk_params (`t.Dict[str, t.Union[str, int]]`):
            Params for sk initialization
        metadata (`t.Optional[t.Dict[str, t.Any]]`, default to `None`):
            Custom metadata for given model.

    Returns:
        tag (`str` with a format `name:version`) where `name` is the defined name user
        set for their models, and version will be generated by BentoML.

    Examples:

    """
    context = {"sklearn": sklearn.__version__}
    with model_store.register(
        name,
        module=__name__,
        metadata=metadata,
        framework_context=context,
    ) as ctx:
        joblib.dump(model, os.path.join(ctx.path, f"{SAVE_NAMESPACE}.pkl"))
        return ctx.tag


class _SklearnRunner(Runner):
    def __init__(
        self,
        tag: str,
        resource_quota: t.Dict[str, t.Any],
        batch_options: t.Dict[str, t.Any],
        model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
    ):
        super().__init__(tag, resource_quota, batch_options)
        model_info, model_file = _get_model_info(tag, model_store)
        self._model_info = model_info
        self._model_file = model_file
        self._parallel_ctx = parallel_backend(
            "threading", n_jobs=self.num_concurrency_per_replica
        )

    @property
    def num_concurrency_per_replica(self) -> int:
        # NOTE: Sklearn doesn't use GPU, so return max. no. of CPU's.
        return int(round(self.resource_quota.cpu))

    @property
    def num_replica(self) -> int:
        # NOTE: SKlearn doesn't use GPU, so just return 1.
        return 1

    @property
    def required_models(self):
        return [self._model_info.tag]

    def _setup(self) -> None:
        gpu_device_id = self.resource_quota.gpus[self.replica_id]

        self._model = joblib.load(filename=model_file)

    def _run_batch(
        self, input_data: t.Union[np.ndarray, "pd.DataFrame"]
    ) -> "np.ndarray":
        with self._parallel_ctx:
            return self._model.predict(input_data)


@inject
def load_runner(
    tag: str,
    *,
    resource_quota: t.Dict[str, t.Any] = None,
    batch_options: t.Dict[str, t.Any] = None,
    model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
) -> "_SklearnRunner":

    """
    Runner represents a unit of serving logic that can be scaled horizontally to
    maximize throughput. `bentoml.sklearn.load_runner` implements a Runner class that
    wrap around a Sklearn joblib model, which optimize it for the BentoML runtime.

    Returns:
        Runner instances for the target `bentml.sklearn` model

    Examples::
        import bentoml
        import bentoml.sklearn
        import numpy as np

        from bentoml.io import NumpyNdarray

        input_data = NumpyNdarray()
        runner = bentoml.sklearn.load_runner("my_model:20201012_DE43A2")
        runner.run(input_data)
    """
    return _SklearnRunner(
        tag=tag,
        resource_quota=resource_quota,
        batch_options=batch_options,
        model_store=model_store,
    )
