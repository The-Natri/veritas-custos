use odra::prelude::*;

#[odra::odra_type]
pub enum TrustTier {
    Rejected,
    Low,
    Medium,
    High,
}

#[odra::odra_type]
pub struct AgentRecord {
    pub agent_address: Address,
    pub score: u32,
    pub tier: TrustTier,
    pub job_count: u32,
    pub registered_at: u64,
    pub last_updated_at: u64,
}

#[odra::odra_error]
pub enum Error {
    NotAdmin = 1,
    AgentAlreadyRegistered = 2,
    AgentNotFound = 3,
}

#[odra::event]
pub struct AgentRegistered {
    pub agent_address: Address,
    pub initial_score: u32,
    pub tier: TrustTier,
    pub timestamp: u64,
}

#[odra::event]
pub struct ReputationUpdated {
    pub agent_address: Address,
    pub new_score: u32,
    pub tier: TrustTier,
    pub job_count: u32,
    pub timestamp: u64,
}

#[odra::module(events = [AgentRegistered, ReputationUpdated])]
pub struct VeritasCustos {
    admin: Var<Address>,
    agents: Mapping<Address, AgentRecord>,
}

#[odra::module]
impl VeritasCustos {
    pub fn init(&mut self) {
        let caller = self.env().caller();
        self.admin.set(caller);
    }

    pub fn register_agent(&mut self, agent_address: Address, initial_score: u32, tier: TrustTier) {
        self.assert_admin();

        if self.agents.get(&agent_address).is_some() {
            self.env().revert(Error::AgentAlreadyRegistered);
        }

        let now = self.env().get_block_time();

        let record = AgentRecord {
            agent_address,
            score: initial_score,
            tier: tier.clone(),
            job_count: 0,
            registered_at: now,
            last_updated_at: now,
        };

        self.agents.set(&agent_address, record);

        self.env().emit_event(AgentRegistered {
            agent_address,
            initial_score,
            tier,
            timestamp: now,
        });
    }

    pub fn update_reputation(&mut self, agent_address: Address, new_score: u32, tier: TrustTier) {
        self.assert_admin();

        let mut record = match self.agents.get(&agent_address) {
            Some(r) => r,
            None => self.env().revert(Error::AgentNotFound),
        };

        let now = self.env().get_block_time();

        record.score = new_score;
        record.tier = tier.clone();
        record.job_count += 1;
        record.last_updated_at = now;

        self.agents.set(&agent_address, record.clone());

        self.env().emit_event(ReputationUpdated {
            agent_address,
            new_score,
            tier,
            job_count: record.job_count,
            timestamp: now,
        });
    }

    pub fn get_reputation(&self, agent_address: Address) -> Option<AgentRecord> {
        self.agents.get(&agent_address)
    }

    pub fn get_admin(&self) -> Option<Address> {
        self.admin.get()
    }

    fn assert_admin(&self) {
        let caller = self.env().caller();
        let admin = self.admin.get();
        if admin != Some(caller) {
            self.env().revert(Error::NotAdmin);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use odra::host::{Deployer, NoArgs};

    #[test]
    fn register_and_read_agent() {
        let env = odra_test::env();
        let mut contract = VeritasCustos::deploy(&env, NoArgs);
        let agent = env.get_account(1);
        contract.register_agent(agent, 50, TrustTier::Medium);
        let record = contract.get_reputation(agent).unwrap();
        assert_eq!(record.score, 50);
        assert_eq!(record.job_count, 0);
    }

    #[test]
    fn update_reputation_increments_job_count() {
        let env = odra_test::env();
        let mut contract = VeritasCustos::deploy(&env, NoArgs);
        let agent = env.get_account(1);
        contract.register_agent(agent, 50, TrustTier::Medium);
        contract.update_reputation(agent, 75, TrustTier::High);
        let record = contract.get_reputation(agent).unwrap();
        assert_eq!(record.score, 75);
        assert_eq!(record.job_count, 1);
    }

    #[test]
    fn non_admin_cannot_register() {
        let env = odra_test::env();
        let mut contract = VeritasCustos::deploy(&env, NoArgs);
        env.set_caller(env.get_account(2));
        let agent = env.get_account(1);
        let result = contract.try_register_agent(agent, 50, TrustTier::Medium);
        assert!(result.is_err());
    }
}
