//! This example demonstrates how to use the `odra-cli` tool to deploy and interact with a smart contract.

use veritas_custos::veritas_custos::VeritasCustos;
use odra::host::{HostEnv, NoArgs};
use odra_cli::{
    deploy::DeployScript,
    scenario::{Args, Error, Scenario, ScenarioMetadata},
    CommandArg, ContractProvider, DeployedContractsContainer, DeployerExt,
    OdraCli,
};

/// Deploys the `VeritasCustos` contract.
pub struct ContractsDeployScript;

impl DeployScript for ContractsDeployScript {
    fn deploy(
        &self,
        env: &HostEnv,
        container: &mut DeployedContractsContainer,
    ) -> Result<(), odra_cli::deploy::Error> {
        let _ = VeritasCustos::load_or_deploy(
            &env,
            NoArgs,
            container,
            500_000_000_000, // Adjust gas limit as needed
        )?;

        Ok(())
    }
}

/// Scenario that registers a test agent.
pub struct RegisterTestAgent;

impl Scenario for RegisterTestAgent {
    fn args(&self) -> Vec<CommandArg> {
        vec![]
    }

    fn run(
        &self,
        env: &HostEnv,
        container: &DeployedContractsContainer,
        _args: Args,
    ) -> Result<(), Error> {
        let mut contract = container.contract_ref::<VeritasCustos>(env)?;

        env.set_gas(50_000_000);
        
        let agent = env.get_account(1);
        use veritas_custos::veritas_custos::TrustTier;
        contract.try_register_agent(agent, 100, TrustTier::High)?;

        Ok(())
    }
}

impl ScenarioMetadata for RegisterTestAgent {
    const NAME: &'static str = "register_test_agent";
    const DESCRIPTION: &'static str = "Registers a test agent with VeritasCustos.";
}

/// Main function to run the CLI tool.
pub fn main() {
    OdraCli::new()
        .about("CLI tool for veritas_custos_contract smart contract")
        .deploy(ContractsDeployScript)
        .contract::<VeritasCustos>()
        .scenario(RegisterTestAgent)
        .build()
        .run();
}
